<p align="center">
  <img src="docs/assets/nex-logo.png" alt="NEX — Neural Execution Runtime. Keep the good work." width="820">
</p>

**Let models predict. Let the runtime keep the good work.**

English · [简体中文](README.zh-CN.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-123F70.svg)](LICENSE)

One model-generated rule. Many tool calls. One late counterexample.
**Why redo the work that already passed validation?**

NEX executes small model-authored workflows with **semantic retirement and bounded recovery**. The model supplies predictions and repairs; the runtime owns validation, snapshots, recovery scope, and publication.

Python 3.10–3.13 · pluggable model backends · evidence-gated publication.

## Run a workflow

```bash
git clone https://github.com/yangforever17/nex.git
cd nex
python3 -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
python -m pip install -e .

nex demo --backend fixture --output artifacts/first-run
```

Choose where predictions run. Every backend uses the same workflow, tool adapter, validation, and recovery policy.

| Backend | Model execution | Setup |
|---|---|---|
| `fixture` | Deterministic decisions for repeatable checks | Core installation |
| `local` | A local instruction-tuned model through Transformers | `pip install -e '.[gpu]'` |
| `api` | A hosted or self-served Chat Completions endpoint | Base URL, model ID, API key for remote services |

### Local GPU

```bash
python -m pip install -e '.[gpu]'
nex demo --backend local --model Qwen/Qwen3-1.7B \
  --device cuda:0 --output artifacts/local-run
```

Use a Hugging Face model ID or a local checkpoint directory. `--local-files-only` disables downloading; `--device auto` enables automatic placement and `--device cpu` selects CPU explicitly. The model is loaded once and reused for prediction and repair. See the [Qwen model card](https://huggingface.co/Qwen/Qwen3-1.7B) for checkpoint details.

### API service

Configure `NEX_BASE_URL`, `NEX_MODEL`, and `NEX_API_KEY` in your environment, then run:

```bash
nex demo --backend api --output artifacts/api-run
```

The transport targets `/chat/completions`; include `/v1` in the base URL if your server requires it. It works with this protocol rather than a vendor-specific SDK. For an existing local [vLLM endpoint](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/):

```bash
nex demo --backend api --base-url http://localhost:8000/v1 \
  --model Qwen/Qwen3-1.7B --disable-thinking --output artifacts/server-run
```

`--json-mode` requests JSON output on supporting servers. `--disable-thinking` sends the vLLM chat-template extension; omit it for other APIs. Provider setup and the shared Python interface are in the [integration guide](docs/integration.md).

### Inspect the run

The bundled program migrates 16 private JSON files, including one different source unit. The program is fixed; local/API models supply real predictions and bounded repairs. Outputs are checked against the same tool contract—invalid model responses fail closed, with no fallback to fixture answers.

```bash
# workspace/           private JSON files
# trace.jsonl          certificate / exception / resume / publication events
# summary.json         outcome, operation counts, backend, latency and token usage
# publications.sqlite  idempotent local publication sink
```

Output directories must be new. Without `--output`, a temporary workspace is cleaned after execution. Token counts are recorded when available; absent API usage stays `null`. First-call timing includes local model loading. Real-model outcomes and costs may differ from the fixture.

## How it works

```python
def migrate(sites):
    observations = observe(sites[:2])
    rule = semantic(observations, "Select the source-unit migration rule for these observations")
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

`StructuredProvider` binds a model backend to your tool's decision vocabulary. Changing the backend does not change the runtime interface:

```python
from nex import StructuredProvider, TransformersBackend
from nex.demo import DECISIONS

backend = TransformersBackend("Qwen/Qwen3-1.7B", device="cuda:0")
provider = StructuredProvider(backend, DECISIONS)
# Pass provider to Runtime alongside your workflow, adapter, and ledger.
```

Use `APIBackend` for a service, or `CallbackProvider` for an existing model client. Implement `MigrationAdapter` for your tools. Runnable runtime example: [examples/run_migration.py](examples/run_migration.py).

## Tests and experiments

```bash
python -m pip install -e '.[dev,experiments]'
python -m pytest -q
ruff check .
python examples/run_migration.py

nex benchmark --sizes 8 16 32 64
nex demo --policy full-retry
nex demo --global-only
nex analyze examples/migration.py

# Optional, independent graph conformance and conservative-envelope sweeps:
python -m nex.experiments.conformance --seeds-per-cell 1
python -m nex.experiments.envelopes
```

The default fixture rolls back 5 sites under NEX versus 11 under Full Retry; with only global evidence, NEX rolls back all 16. These are deterministic operation counts, not measured LLM speedups. Graph sweeps are independent controlled experiments. Tests include real HTTP transport checks with a scripted server and optional [live-model checks](tests/test_live.py).

## Code map

| Module | Responsibility |
|---|---|
| [compiler.py](src/nex/compiler.py) · [analysis.py](src/nex/analysis.py) | Restricted workflow grammar and fan-out analysis |
| [runtime.py](src/nex/runtime.py) | Certificates, snapshots, recovery, and publication gate |
| [providers.py](src/nex/providers.py) · [backends.py](src/nex/backends.py) | Shared decision protocol; local and API inference |
| [ledger.py](src/nex/ledger.py) | Transactional local publication sink |
| [demo.py](src/nex/demo.py) · [cli.py](src/nex/cli.py) | Runnable examples and controlled comparisons |
| [experiments/](src/nex/experiments/) · [tests/](tests/) | Independent graph checks and regression tests |

## Scope

Research reference implementation, **not a Python sandbox**. The executable backend supports the finite workflow above and independent recovery sites; adapters and callbacks are trusted host code. It does not provide arbitrary-Python taint tracking, process-crash continuation recovery, or remote HTTP exactly-once delivery. SQLite insertion is the local publication effect, not an atomic wrapper around a remote request.

[Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Apache-2.0](LICENSE)
