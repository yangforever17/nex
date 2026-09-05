"""Chat transports with bounded generation and explicit, secret-free telemetry."""

from dataclasses import asdict, dataclass
import ipaddress
import json
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class BackendError(RuntimeError):
    """A transport or inference failure; never a request to use fixture output."""


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class GenerationRecord:
    success: bool
    elapsed_s: float
    input_tokens: int | None
    output_tokens: int | None


class ChatBackend:
    """Synchronous transport. Records include first-call model loading, if any."""

    kind = "custom"

    def __init__(self, model: str, *, max_tokens: int = 512):
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a nonempty model ID or local directory")
        if type(max_tokens) is not int or not 16 <= max_tokens <= 32768:
            raise ValueError("max_tokens must be an integer from 16 to 32768")
        self.model, self.max_tokens = model, max_tokens
        self.records: list[GenerationRecord] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        if len(json.dumps(messages).encode()) > 512_000:
            raise BackendError("model request exceeds the 512 KB input limit")
        start = time.perf_counter()
        result = None
        try:
            result = self._generate(messages)
            return result.text
        finally:
            self.records.append(GenerationRecord(
                result is not None, time.perf_counter() - start,
                result.input_tokens if result else None, result.output_tokens if result else None))

    def _generate(self, messages: list[dict[str, str]]) -> Completion:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"backend": self.kind, "model": self.model, "max_tokens": self.max_tokens,
                "calls": [asdict(record) for record in self.records]}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BackendError("API redirects are disabled; use the final base URL")


class APIBackend(ChatBackend):
    """Chat Completions transport; HTTPS except for explicit loopback servers.

    Credentials stay in memory, redirects are blocked, and failures are not
    retried automatically. Response bodies and headers are never in errors.
    """

    kind = "api"

    def __init__(self, model: str, *, base_url: str, api_key: str | None = None,
                 timeout: float = 120, max_tokens: int = 512, json_mode: bool = False,
                 disable_thinking: bool = False):
        super().__init__(model, max_tokens=max_tokens)
        url = urlsplit(base_url)
        try:
            loopback = url.hostname == "localhost" or ipaddress.ip_address(url.hostname or "").is_loopback
        except ValueError:
            loopback = False
        if (not url.hostname or url.scheme not in {"http", "https"} or url.username is not None
                or url.password is not None or url.query or url.fragment
                or (url.scheme == "http" and not loopback)):
            raise ValueError("base_url requires HTTPS (or loopback HTTP), without credentials, query or fragment")
        if not loopback and not api_key:
            raise ValueError("set the API key environment variable for a remote endpoint")
        if api_key is not None and (not isinstance(api_key, str) or any(c in api_key for c in "\r\n")):
            raise ValueError("invalid API key")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise ValueError("timeout must be between 0 and 3600 seconds")
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self.timeout, self.json_mode, self.disable_thinking = timeout, json_mode, disable_thinking
        self._opener = build_opener(_NoRedirect())

    def _generate(self, messages: list[dict[str, str]]) -> Completion:
        payload = {"model": self.model, "messages": messages, "temperature": 0,
                   "max_tokens": self.max_tokens, "stream": False}
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.disable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = Request(self.endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(1_048_577)
        except HTTPError as exc:
            raise BackendError(f"API HTTP {exc.code}; check credentials, model, quota and endpoint options") from None
        except (URLError, OSError, TimeoutError):
            raise BackendError("API connection failed or timed out; request was not retried") from None
        if len(raw) > 1_048_576:
            raise BackendError("API response exceeds the 1 MB limit")
        try:
            data = json.loads(raw)
            choice = data["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise BackendError("API completion did not finish normally; no decision accepted")
            text = choice["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise BackendError("API returned no text decision")
            usage = data.get("usage") or {}
            counts = [usage.get(name) for name in ("prompt_tokens", "completion_tokens")]
            counts = [n if type(n) is int and n >= 0 else None for n in counts]
            return Completion(text, *counts)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise BackendError("API returned an invalid Chat Completions response") from None


class TransformersBackend(ChatBackend):
    """Lazy, reusable local model; explicit device, chat template, safetensors only."""

    kind = "local"

    def __init__(self, model: str, *, device: str = "cuda:0", max_tokens: int = 512,
                 max_input_tokens: int = 8192, local_files_only: bool = False,
                 revision: str | None = None):
        super().__init__(model, max_tokens=max_tokens)
        if not re.fullmatch(r"cpu|auto|cuda(?::[0-9]+)?", device):
            raise ValueError("device must be cpu, auto, cuda, or cuda:N")
        if type(max_input_tokens) is not int or not 32 <= max_input_tokens <= 131072:
            raise ValueError("max_input_tokens must be from 32 to 131072")
        self.device, self.max_input_tokens = device, max_input_tokens
        self.local_files_only, self.revision = local_files_only, revision
        self._model = self._tokenizer = self._torch = None

    def _load(self):
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError:
            raise BackendError("local inference requires: python -m pip install -e '.[gpu]'") from None
        if self.device.startswith("cuda") and not torch.cuda.is_available():
            raise BackendError("CUDA is unavailable; install CUDA-enabled PyTorch or explicitly select --device cpu")
        options = {"trust_remote_code": False, "local_files_only": self.local_files_only,
                   "revision": self.revision}
        try:
            tokenizer = AutoTokenizer.from_pretrained(self.model, **options)
            if not tokenizer.chat_template:
                raise BackendError("model must provide a chat template; use an instruction-tuned checkpoint")
            model = AutoModelForCausalLM.from_pretrained(
                self.model, device_map=self.device, dtype="auto", use_safetensors=True, **options)
            model.eval()
        except (OSError, ValueError, RuntimeError) as exc:
            if isinstance(exc, BackendError):
                raise
            raise BackendError("local model loading failed; check model files, optional dependencies and device memory") from None
        self._torch, self._tokenizer, self._model = torch, tokenizer, model

    def _generate(self, messages: list[dict[str, str]]) -> Completion:
        self._load()
        try:
            inputs = self._tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True, return_dict=True,
                return_tensors="pt", enable_thinking=False)
            n_input = inputs["input_ids"].shape[-1]
            context = getattr(self._model.config, "max_position_embeddings", self.max_input_tokens + self.max_tokens)
            if n_input > min(self.max_input_tokens, context - self.max_tokens):
                raise BackendError("local model input exceeds the context budget; reduce the recovery batch")
            inputs = inputs.to(self._model.device)
            with self._torch.inference_mode():
                output = self._model.generate(**inputs, max_new_tokens=self.max_tokens, do_sample=False,
                                              temperature=None, top_p=None, top_k=None,
                                              pad_token_id=self._tokenizer.eos_token_id)
            tokens = output[0][n_input:]
            if len(tokens) >= self.max_tokens:
                raise BackendError("local generation reached its token limit; no decision accepted")
            return Completion(self._tokenizer.decode(tokens, skip_special_tokens=True), n_input, len(tokens))
        except (ValueError, RuntimeError) as exc:
            if isinstance(exc, BackendError):
                raise
            raise BackendError("local inference failed; check context length and device memory") from None

    def describe(self) -> dict:
        return {**super().describe(), "device": self.device, "revision": self.revision,
                "local_files_only": self.local_files_only}
