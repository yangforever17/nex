"""One migration runner for fixture, local-GPU and API model backends."""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from .analysis import analyze_file
from .backends import APIBackend, TransformersBackend
from .compiler import WorkflowCompiler
from .demo import DECISIONS, DemoProvider, JsonMigrationAdapter, WORKFLOW
from .ledger import PublicationLedger
from .providers import PredictionProvider, StructuredProvider
from .runtime import RecoveryPolicy, Runtime


def run_demo(root: Path, *, sites: int, failure: int, delay: int,
             policy: str, local: bool = True, provider: PredictionProvider | None = None) -> dict:
    provider = provider if provider is not None else DemoProvider()
    adapter = JsonMigrationAdapter(root / "workspace", n_sites=sites,
                                   failure_site=failure or None, local_certificates=local)
    runtime = Runtime(WorkflowCompiler().compile(WORKFLOW), adapter, provider,
                      PublicationLedger(root / "publications.sqlite"),
                      policy=policy, guard_delay=delay)
    result = runtime.execute()
    (root / "trace.jsonl").write_text("".join(json.dumps(e) + "\n" for e in result.events), encoding="utf-8")
    summary = {"success": result.success, "policy": result.policy,
               "program_sha256": result.program_sha256, "error": result.error,
               "elapsed_s": result.elapsed_s, "metrics": asdict(result.metrics),
               "provider": (provider.backend.describe() if isinstance(provider, StructuredProvider)
                            else {"backend": "fixture" if isinstance(provider, DemoProvider) else "callback"})}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _provider(args) -> PredictionProvider:
    if args.backend == "fixture":
        if any((args.model, args.base_url, args.device, args.local_files_only, args.revision,
                args.json_mode, args.disable_thinking)):
            raise ValueError("select --backend local or api when supplying model options")
        return DemoProvider()
    model = args.model or os.environ.get("NEX_MODEL")
    if not model:
        raise ValueError("set --model or NEX_MODEL for a real model backend")
    if args.backend == "api":
        if args.device or args.local_files_only or args.revision:
            raise ValueError("device and model-file options require --backend local")
        base_url = args.base_url or os.environ.get("NEX_BASE_URL")
        if not base_url:
            raise ValueError("set --base-url or NEX_BASE_URL for the API backend")
        backend = APIBackend(model, base_url=base_url, api_key=os.environ.get(args.api_key_env),
                             timeout=args.timeout, max_tokens=args.max_new_tokens,
                             json_mode=args.json_mode, disable_thinking=args.disable_thinking)
    else:
        if args.base_url or args.json_mode or args.disable_thinking:
            raise ValueError("endpoint options require --backend api; local thinking is already disabled")
        backend = TransformersBackend(model, device=args.device or "cuda:0", max_tokens=args.max_new_tokens,
                                      max_input_tokens=args.max_input_tokens,
                                      local_files_only=args.local_files_only, revision=args.revision)
    return StructuredProvider(backend, DECISIONS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nex", description="Evidence-gated model-authored workflow execution")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run a migration using fixture, local-GPU or API predictions")
    model = demo.add_argument_group("model backend")
    model.add_argument("--backend", choices=["fixture", "local", "api"], default="fixture")
    model.add_argument("--model", help="model ID or local path; defaults to NEX_MODEL")
    model.add_argument("--max-new-tokens", type=int, default=512)
    model.add_argument("--device", help="local device: cuda:0 (default), cpu, or auto for sharding")
    model.add_argument("--max-input-tokens", type=int, default=8192, help="local context budget; never truncates")
    model.add_argument("--local-files-only", action="store_true", help="load cached local model files only")
    model.add_argument("--revision", help="local checkpoint revision, preferably a commit hash")
    model.add_argument("--base-url", help="Chat Completions base URL, including /v1 if needed; or NEX_BASE_URL")
    model.add_argument("--api-key-env", default="NEX_API_KEY", help="environment variable name, not the secret")
    model.add_argument("--timeout", type=float, default=120, help="API request timeout in seconds; no retries")
    model.add_argument("--json-mode", action="store_true", help="request JSON mode on compatible API servers")
    model.add_argument("--disable-thinking", action="store_true", help="send vLLM chat_template_kwargs extension")
    demo.add_argument("--sites", type=int, default=16)
    demo.add_argument("--failure-site", type=int, default=7, help="one-based exceptional site; 0 means no fault")
    demo.add_argument("--guard-delay", type=int, default=4)
    demo.add_argument("--policy", choices=[p.value for p in RecoveryPolicy], default="nex")
    demo.add_argument("--global-only", action="store_true", help="local checks return UNKNOWN")
    demo.add_argument("--output", type=Path, help="new directory; never overwrites an existing directory")
    analyze = commands.add_parser("analyze", help="inspect dataflow without executing the source")
    analyze.add_argument("source", type=Path)
    bench = commands.add_parser("benchmark", help="paired controlled comparison; not LLM wall-clock speedup")
    bench.add_argument("--sizes", nargs="+", type=int, default=[8, 16, 32, 64])
    bench.add_argument("--guard-delay", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            print(json.dumps(analyze_file(args.source), indent=2))
            return 0
        if args.command == "demo":
            provider = _provider(args)
            if args.output:
                args.output.mkdir(parents=True, exist_ok=False)
                summary = run_demo(args.output, sites=args.sites, failure=args.failure_site,
                                   delay=args.guard_delay, policy=args.policy, local=not args.global_only,
                                   provider=provider)
            else:
                with tempfile.TemporaryDirectory(prefix="nex-demo-") as directory:
                    summary = run_demo(Path(directory), sites=args.sites, failure=args.failure_site,
                                       delay=args.guard_delay, policy=args.policy, local=not args.global_only,
                                       provider=provider)
            print(json.dumps(summary, indent=2))
            return 0 if summary["success"] else 1
        if any(not 4 <= n <= 1024 for n in args.sizes):
            parser.error("benchmark sizes must be between 4 and 1024")
        if not 0 <= args.guard_delay <= 10000:
            parser.error("guard delay must be from 0 to 10000")
        rows = []
        with tempfile.TemporaryDirectory(prefix="nex-benchmark-") as directory:
            for index, n in enumerate(args.sizes):
                failure = max(1, n // 2)
                for policy in RecoveryPolicy:
                    root = Path(directory) / f"{index}-{policy.value}"
                    root.mkdir()
                    summary = run_demo(root, sites=n, failure=failure,
                                       delay=args.guard_delay, policy=policy.value)
                    if not summary["success"]:
                        raise RuntimeError(summary["error"])
                    rows.append({"sites": n, "failure_site": failure,
                                 "policy": policy.value, **summary["metrics"]})
        print(json.dumps({"evidence": "controlled; deterministic provider; same program and fault",
                          "rows": rows}, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError, SyntaxError) as exc:
        parser.exit(1, f"nex: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
