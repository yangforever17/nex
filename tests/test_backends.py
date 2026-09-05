from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import threading
from types import SimpleNamespace

import pytest

from nex import APIBackend, BackendError, TransformersBackend
from nex.cli import main


def response(content='{"decision":"milliseconds"}', **updates):
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {"prompt_tokens": 25, "completion_tokens": 8}, **updates}


@contextmanager
def chat_server(replies):
    requests = []
    replies = iter(replies)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            status, payload = next(replies)
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "/other-endpoint")
            self.end_headers()
            self.wfile.write(payload if isinstance(payload, bytes) else json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_http_transport_payload_and_redacted_telemetry():
    with chat_server([(200, response())]) as (url, requests):
        backend = APIBackend("served-model", base_url=url, api_key="test-secret-value",
                             json_mode=True, disable_thinking=True)
        assert backend.complete([{"role": "user", "content": "json please"}]) == '{"decision":"milliseconds"}'
    path, headers, body = requests[0]
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == "Bearer test-secret-value"
    assert body["response_format"] == {"type": "json_object"}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["stream"] is False
    metadata = backend.describe()
    assert metadata["calls"][0]["input_tokens"] == 25
    assert metadata["calls"][0]["output_tokens"] == 8
    assert "test-secret-value" not in json.dumps(metadata)


def test_api_cli_uses_real_http_for_prediction_and_recovery(tmp_path, capsys):
    with chat_server([(200, response()), (200, response('{"decisions":["seconds"]}'))]) as (url, requests):
        assert main(["demo", "--backend", "api", "--model", "served-model", "--base-url", url,
                     "--output", str(tmp_path / "run")]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert len(requests) == 2
    assert summary["provider"]["backend"] == "api"
    assert summary["metrics"]["publications"] == 1
    assert summary["metrics"]["rolled_back_sites"] == 5


@pytest.mark.parametrize("status,payload", [(401, b"test-secret-value"), (429, b"quota"),
    (302, b"redirect"), (200, b"not json"), (200, {}), (200, {"choices": []}),
    (200, response(choices=[{"finish_reason": "length", "message": {"content": "partial"}}])),
    (200, response(content=None)), (200, response(usage=[])), (200, b"x" * 1_048_577)])
def test_api_failures_are_bounded_and_not_retried(status, payload):
    # Empty usage is allowed as absent, so use an invalid nonempty list below.
    if isinstance(payload, dict) and payload.get("usage") == []:
        payload = {**payload, "usage": [1]}
    with chat_server([(status, payload)]) as (url, requests):
        backend = APIBackend("model", base_url=url, api_key="test-secret-value")
        with pytest.raises(BackendError) as error:
            backend.complete([])
    assert len(requests) == 1
    assert "test-secret-value" not in str(error.value)
    assert not backend.records[0].success


@pytest.mark.parametrize("url", ["http://example.com/v1", "ftp://example.com", "https://user:secret@example.com",
                                    "https://example.com?secret=x", "https://example.com/#secret", ""])
def test_unsafe_api_configuration_rejected(url):
    with pytest.raises(ValueError):
        APIBackend("model", base_url=url, api_key="test-secret")


def test_remote_key_required_and_unknown_usage_not_fabricated():
    with pytest.raises(ValueError, match="API key"):
        APIBackend("model", base_url="https://example.com/v1")
    with chat_server([(200, response(usage={"prompt_tokens": True, "completion_tokens": -1}))]) as (url, _):
        backend = APIBackend("model", base_url=url)
        backend.complete([])
    assert backend.records[0].input_tokens is None
    assert backend.records[0].output_tokens is None


@pytest.mark.parametrize("options", [{"timeout": 0}, {"timeout": float("nan")}, {"timeout": True},
    {"api_key": "bad\nheader"}, {"max_tokens": 0}, {"max_tokens": True}])
def test_api_configuration_bounds(options):
    with pytest.raises(ValueError):
        APIBackend("model", base_url="http://localhost/v1", **options)


def test_api_connection_error_and_input_limit(monkeypatch):
    backend = APIBackend("model", base_url="http://localhost/v1")
    def fail(*args, **kwargs):
        raise TimeoutError("sensitive payload")
    monkeypatch.setattr(backend._opener, "open", fail)
    with pytest.raises(BackendError, match="timed out"):
        backend.complete([])
    with pytest.raises(BackendError, match="input limit"):
        backend.complete([{"role": "user", "content": "x" * 512001}])
    assert len(backend.records) == 1


@pytest.fixture
def local_dependencies(monkeypatch):
    calls = []
    inputs = type("Inputs", (dict,), {"to": lambda self, device: self})({"input_ids": SimpleNamespace(shape=(1, 2))})
    tokenizer = SimpleNamespace(chat_template="template", eos_token_id=0,
        apply_chat_template=lambda *args, **kwargs: inputs, decode=lambda tokens, **kwargs: '{"decision":"seconds"}')
    model = SimpleNamespace(config=SimpleNamespace(max_position_embeddings=1024), device="cuda:0",
                            eval=lambda: None, generate=lambda **kwargs: [[1, 2, 3, 0]])
    def load_tokenizer(*args, **kwargs):
        calls.append(("tokenizer", kwargs))
        return tokenizer
    def load_model(*args, **kwargs):
        calls.append(("model", kwargs))
        return model
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True), inference_mode=nullcontext)
    transformers = SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=load_tokenizer),
                                   AutoModelForCausalLM=SimpleNamespace(from_pretrained=load_model))
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    return calls, tokenizer, model, torch


def test_local_lazy_load_and_reuse(local_dependencies):
    calls, _, _, _ = local_dependencies
    backend = TransformersBackend("local-model", local_files_only=True, revision="pinned")
    assert calls == []
    for _ in range(2):
        assert backend.complete([]) == '{"decision":"seconds"}'
    assert len(calls) == 2
    assert all(not opts["trust_remote_code"] and opts["local_files_only"] for _, opts in calls)
    assert calls[1][1]["use_safetensors"] is True
    assert backend.describe()["device"] == "cuda:0"
    assert backend.records[0].input_tokens == 2
    assert backend.records[0].output_tokens == 2


@pytest.mark.parametrize("case", ["no-cuda", "no-template", "context", "truncation", "oom"])
def test_local_fail_closed(local_dependencies, case):
    _, tokenizer, model, torch = local_dependencies
    if case == "no-cuda":
        torch.cuda.is_available = lambda: False
    elif case == "no-template":
        tokenizer.chat_template = None
    elif case == "context":
        model.config.max_position_embeddings = 16
    elif case == "truncation":
        model.generate = lambda **kwargs: [list(range(514))]
    else:
        def oom(**kwargs):
            raise RuntimeError("private local details")
        model.generate = oom
    backend = TransformersBackend("local-model")
    with pytest.raises(BackendError):
        backend.complete([])
    assert not backend.records[0].success


def test_missing_optional_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(BackendError, match="gpu"):
        TransformersBackend("model").complete([])


@pytest.mark.parametrize("options", [{"device": "bad"}, {"max_input_tokens": 0}, {"max_tokens": -1}])
def test_local_configuration_bounds(options):
    with pytest.raises(ValueError):
        TransformersBackend("model", **options)
