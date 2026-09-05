"""Offline demos, controlled comparisons, and diagnostic source analysis."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import tempfile

from .analysis import analyze_file
from .compiler import WorkflowCompiler
from .demo import DemoProvider, JsonMigrationAdapter, WORKFLOW
from .ledger import PublicationLedger
from .runtime import RecoveryPolicy, Runtime


def run_demo(root: Path, *, sites: int, failure: int, delay: int,
             policy: str, local: bool = True) -> dict:
    adapter = JsonMigrationAdapter(root / "workspace", n_sites=sites,
                                   failure_site=failure or None, local_certificates=local)
    runtime = Runtime(WorkflowCompiler().compile(WORKFLOW), adapter, DemoProvider(),
                      PublicationLedger(root / "publications.sqlite"),
                      policy=policy, guard_delay=delay)
    result = runtime.execute()
    (root / "trace.jsonl").write_text("".join(json.dumps(e) + "\n" for e in result.events), encoding="utf-8")
    summary = {"success": result.success, "policy": result.policy,
               "program_sha256": result.program_sha256, "error": result.error,
               "metrics": asdict(result.metrics)}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nex", description="Evidence-gated model-authored workflow execution")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run a deterministic migration with real private file writes")
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
            if args.output:
                args.output.mkdir(parents=True, exist_ok=False)
                summary = run_demo(args.output, sites=args.sites, failure=args.failure_site,
                                   delay=args.guard_delay, policy=args.policy, local=not args.global_only)
            else:
                with tempfile.TemporaryDirectory(prefix="nex-demo-") as directory:
                    summary = run_demo(Path(directory), sites=args.sites, failure=args.failure_site,
                                       delay=args.guard_delay, policy=args.policy, local=not args.global_only)
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
