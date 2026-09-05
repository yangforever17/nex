<p align="center">
  <img src="docs/assets/nex-logo.png" alt="NEX — Neural Execution Runtime。保住正确的工作。" width="820">
</p>

**让模型大胆预测，让运行时保住正确的工作。**

[English](README.md) · 简体中文

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-123F70.svg)](LICENSE)

一个模型规则，多次工具调用，一个晚到反例。**为什么已经验证正确的工作也要重做？**

NEX 为小型模型生成程序提供 **semantic retirement 与受限恢复**：模型提供预测和修复，运行时掌握验证、快照、恢复范围与结果发布。

Python 3.10–3.13 · 可替换的模型后端 · 证据驱动的发布门控。

## 运行一个工作流

```bash
git clone https://github.com/yangforever17/nex.git
cd nex
python3 -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
python -m pip install -e .

nex demo --backend fixture --output artifacts/first-run
```

选择模型在哪里执行。三种后端共享同一份程序、工具 adapter、验证和恢复策略。

| 后端 | 模型执行方式 | 配置 |
|---|---|---|
| `fixture` | 用于可重复检查的确定性决策 | 安装核心包 |
| `local` | Transformers 本地指令模型 | `pip install -e '.[gpu]'` |
| `api` | 托管或自建的 Chat Completions 服务 | Base URL、模型 ID；远端服务另需 API key |

### 本地 GPU

```bash
python -m pip install -e '.[gpu]'
nex demo --backend local --model Qwen/Qwen3-1.7B \
  --device cuda:0 --output artifacts/local-run
```

支持 Hugging Face 模型 ID 或本地 checkpoint 目录。`--local-files-only` 禁止下载；`--device auto` 自动分配设备，`--device cpu` 显式使用 CPU。模型只加载一次，预测和修复复用同一实例。模型细节见 [Qwen 模型卡](https://huggingface.co/Qwen/Qwen3-1.7B)。

### API 服务

在环境中配置 `NEX_BASE_URL`、`NEX_MODEL` 和 `NEX_API_KEY`，然后运行：

```bash
nex demo --backend api --output artifacts/api-run
```

后端请求 `/chat/completions`，服务要求 `/v1` 时需将其写入 base URL；不绑定厂商 SDK。例如接入已有的本地 [vLLM 服务](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)：

```bash
nex demo --backend api --base-url http://localhost:8000/v1 \
  --model Qwen/Qwen3-1.7B --disable-thinking --output artifacts/server-run
```

`--json-mode` 用于支持 JSON 输出的服务；`--disable-thinking` 发送 vLLM chat-template 扩展，其他 API 通常应省略。服务配置与统一 Python 接口见[接入指南](docs/integration.md)。

### 查看结果

示例迁移 16 个私有 JSON 文件，其中一处使用不同源单位。程序本身固定；local/API 后端真实生成语义决策和受限修复。所有输出都经过同一工具契约检查：格式或授权范围不符就失败关闭，不会退回 fixture 答案。

```bash
# workspace/           私有 JSON 文件
# trace.jsonl          certificate / exception / resume / publication 事件
# summary.json         结果、操作计数、后端、调用耗时和 token 用量
# publications.sqlite  本地幂等发布 sink
```

输出目录必须不存在。省略 `--output` 时，运行后清理临时工作区。API 未返回的 token 用量记录为 `null`，不估算补齐；首次本地调用计时包含模型加载。真实模型的结果和成本可能不同于 fixture。

## 工作方式

```python
def migrate(sites):
    observations = observe(sites[:2])
    rule = semantic(observations, "Select the source-unit migration rule for these observations")
    for site in sites:
        apply_change(site, rule)
    publish_report(sites)
    return final_validate()
```

- **Predict**：从 semantic value 的 fan-out 恢复 obligation，不依赖模型自报。
- **Retire**：sound 局部 validator 支持保留版本；`UNKNOWN` 不等于通过。
- **Recover**：恢复未决窗口，只请求 runtime 授权 site 的修复。
- **Publish**：最终任务验证通过后提交，使用 logical ID 对本地 effect 去重。

## 接入模型和工具

`StructuredProvider` 将模型后端绑定到工具的决策词表；切换后端不改变运行时接口：

```python
from nex import StructuredProvider, TransformersBackend
from nex.demo import DECISIONS

backend = TransformersBackend("Qwen/Qwen3-1.7B", device="cuda:0")
provider = StructuredProvider(backend, DECISIONS)
# 将 provider 与 workflow、adapter、ledger 一起传入 Runtime。
```

服务端模型使用 `APIBackend`，已有客户端可用 `CallbackProvider` 包装；自己的工具实现 `MigrationAdapter`。完整运行时示例：[examples/run_migration.py](examples/run_migration.py)。

## 测试与实验

```bash
python -m pip install -e '.[dev,experiments]'
python -m pytest -q
ruff check .
python examples/run_migration.py

nex benchmark --sizes 8 16 32 64
nex demo --policy full-retry
nex demo --global-only
nex analyze examples/migration.py

# 可选：独立 DAG conformance 与 conservative-envelope 扫描
python -m nex.experiments.conformance --seeds-per-cell 1
python -m nex.experiments.envelopes
```

默认 fixture 中 NEX 回滚 5 处、Full Retry 回滚 11 处；只有全局证据时，NEX 回滚全部 16 处。这些是确定性操作计数，不是实测 LLM 加速比。图扫描是独立受控实验。测试包含脚本化服务端的真实 HTTP 链路检查，以及显式启用的[真实模型检查](tests/test_live.py)。

## 代码导航

| 模块 | 职责 |
|---|---|
| [compiler.py](src/nex/compiler.py) · [analysis.py](src/nex/analysis.py) | 受限执行语法与 fan-out 分析 |
| [runtime.py](src/nex/runtime.py) | Certificate、快照、恢复和发布门控 |
| [providers.py](src/nex/providers.py) · [backends.py](src/nex/backends.py) | 统一决策协议、本地与 API 推理 |
| [ledger.py](src/nex/ledger.py) | 本地事务发布 sink |
| [demo.py](src/nex/demo.py) · [cli.py](src/nex/cli.py) | 可运行示例与受控对比 |
| [experiments/](src/nex/experiments/) · [tests/](tests/) | 独立图检查与回归测试 |

## 适用范围

研究型参考实现，**不是 Python 沙箱**。当前 backend 支持上述有限 workflow 与相互独立的恢复 site，adapter 和回调是可信宿主代码。不提供任意 Python taint、进程崩溃后 continuation 恢复或远端 HTTP exactly-once。SQLite 插入本身是本地 publication，不是对远端请求的原子封装。

[安全说明](SECURITY.md) · [贡献指南](CONTRIBUTING.md) · [Apache-2.0](LICENSE)
