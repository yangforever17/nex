<p align="center">
  <img src="docs/assets/nex-logo.png" alt="NEX — Neural Execution Runtime. Keep the good work." width="820">
</p>

**Let models predict. Let the runtime keep the good work.**

English · [简体中文](README.zh-CN.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-123F70.svg)](LICENSE)

One model-generated rule. Many tool calls. One late counterexample.
**Why redo the work that already passed validation?**

NEX executes small model-authored workflows with **semantic retirement and bounded recovery**. The model supplies predictions and repairs; the runtime owns validation, snapshots, recovery scope, and publication.

Python 3.10–3.13 · standard-library core · no GPU or API key needed for the demo.

## Quick start

```bash
git clone https://github.com/yangforever17/nex.git
cd nex
python3 -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
python -m pip install -e .

nex demo
nex demo --policy full-retry
nex demo --global-only
```

The demo writes 16 private JSON files. Site 7 uses a different time unit, and local evidence arrives four operations late:

| Policy | Model callbacks | Rolled-back sites | File writes | Publications |
|---|---:|---:|---:|---:|
| Full Retry | 2 | 11 | 27 | 1 |
| NEX | 2 | 5 | 21 | 1 |
| NEX, global evidence only | 2 | 16 | 32 | 1 |

These are **deterministic demo counts**, not measured LLM speedups. Both policies use the same final-validation publication gate.

Keep the workspace and inspect the trace:

```bash
nex demo --output artifacts/first-run
# workspace/           private JSON files
# trace.jsonl          certificate / exception / resume / publication events
# summary.json         operation counts
# publications.sqlite  idempotent local publication sink
```

The output directory must be new. Without `--output`, the demo uses an automatically cleaned temporary directory.

## How it works

```python
def migrate(sites):
    observations = observe(sites[:2])
    rule = semantic(observations, "Migrate timeouts to seconds")
    for site in sites:
        apply_change(site, rule)
    publish_report(sites)
    return final_validate()
```

- **Predict:** infer obligations from the semantic value's fan-out, without model self-report.
- **Retire:** preserve versions certified by a sound local validator. `UNKNOWN` does not mean success.
- **Recover:** restore the unresolved window and request a repair limited to authorized sites.
- **Publish:** commit only after final task validation; deduplicate the local effect by logical ID.

## Use your model and tools

Implement `PredictionProvider.predict / repair` and `MigrationAdapter`, or wrap an existing model client with `CallbackProvider`. No model vendor is hardcoded.

```python
from nex import CallbackProvider, PublicationLedger, Runtime, WorkflowCompiler

# Your callbacks, adapter, and workflow source:
provider = CallbackProvider(predict_fn=my_predict, repair_fn=my_repair)
runtime = Runtime(
    WorkflowCompiler().compile(workflow_source),
    adapter=my_adapter,
    provider=provider,
    ledger=PublicationLedger("artifacts/publications.sqlite"),
    publication_id="job-001:report",
)
result = runtime.execute()
assert result.success, result.error
```

Runnable example: [examples/run_migration.py](examples/run_migration.py).
Callback signatures and adapter contract: [integration guide](docs/integration.md).

## Tests and experiments

```bash
python -m pip install -e '.[dev,experiments]'
python -m pytest -q
ruff check .
python examples/run_migration.py

nex benchmark --sizes 8 16 32 64
nex analyze examples/migration.py

# Optional, independent graph conformance and conservative-envelope sweeps:
python -m nex.experiments.conformance --seeds-per-cell 1
python -m nex.experiments.envelopes
```

Graph sweeps are controlled experiments, separate from the executable migration backend.

## Code map

| Module | Responsibility |
|---|---|
| [compiler.py](src/nex/compiler.py) · [analysis.py](src/nex/analysis.py) | Restricted workflow grammar and fan-out analysis |
| [runtime.py](src/nex/runtime.py) | Certificates, snapshots, recovery, and publication gate |
| [providers.py](src/nex/providers.py) · [ledger.py](src/nex/ledger.py) | Model callbacks and transactional local sink |
| [demo.py](src/nex/demo.py) · [cli.py](src/nex/cli.py) | Runnable examples and controlled comparisons |
| [experiments/](src/nex/experiments/) · [tests/](tests/) | Independent graph checks and regression tests |

## Scope

Research reference implementation, **not a Python sandbox**. The executable backend supports the finite workflow above and independent recovery sites; adapters and callbacks are trusted host code. It does not provide arbitrary-Python taint tracking, process-crash continuation recovery, or remote HTTP exactly-once delivery. SQLite insertion is the local publication effect, not an atomic wrapper around a remote request.

[Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Apache-2.0](LICENSE)
