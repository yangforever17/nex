# 接入指南

统一链路为：`ChatBackend → StructuredProvider → Runtime → MigrationAdapter / PublicationLedger`。模型后端只提供文本；provider 检查决策词表和数量，并绑定 runtime 授权的 IDs；runtime 持有状态与发布权限。

## 选择推理后端

本地后端安装 `pip install -e '.[gpu]'`，需要与设备匹配的 PyTorch；支持带 chat template 的 causal instruction 模型，拒绝 remote code，仅加载 safetensors。默认显式使用 `cuda:0`；`auto` 可能把部分权重放到 CPU，不能等同于纯 GPU 执行。首次调用加载模型，后续复用，超出 context/token 预算不静默截断。

API 后端使用标准库 HTTP 客户端，按服务端文档设置变量（以下为 Bash）：

```bash
export NEX_BASE_URL="https://api.deepseek.com"
export NEX_MODEL="your-provider-model-id"
read -rsp "API key: " NEX_API_KEY
export NEX_API_KEY
nex demo --backend api --json-mode --output artifacts/api-run
```

模型 ID 以服务商提供的可用列表为准；`--json-mode` 对应 [DeepSeek JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)，非所有兼容接口都支持。自建 [vLLM 服务](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)通常使用 `http://localhost:8000/v1`；Qwen3 等模板可用 `--disable-thinking` 显式关闭 thinking。`--api-key-env` 接收环境变量**名称**，不接收密钥本身。

远端必须 HTTPS 且有密钥；明文 HTTP 只允许 loopback。API 会发送本次预测/修复所需 observation，请先确认数据可交给该服务。请求不自动重试，HTTP 错误不回显正文或认证头，重定向被拒绝。`--timeout` 控制网络请求，`--max-new-tokens` 控制生成上限。返回值必须是完整 JSON，非法决策、重复 JSON key 或错误的值数量会终止执行。

## 同一个 Python 接口

```python
import os
from nex import APIBackend, StructuredProvider
from nex.demo import DECISIONS

backend = APIBackend(
    os.environ["NEX_MODEL"], base_url=os.environ["NEX_BASE_URL"],
    api_key=os.environ.get("NEX_API_KEY"),
)
provider = StructuredProvider(backend, DECISIONS)
```

将 `backend` 换成 `TransformersBackend(model, device="cuda:0")` 即切换本地推理。完整 `Runtime` 组装见 [run_migration.py](../examples/run_migration.py)。更换任务时应一起更换 `DECISIONS`（label→工具语义），不要沿用迁移任务的单位词表。

## 模型回调

```python
predict(observations: tuple[str, ...], question: str) -> str
repair(request: RepairRequest) -> dict[str, str]
```

`RepairRequest` 包含 `site_ids`、恢复后对应的 `observations` 和 `reason`。回调返回字典必须恰好覆盖授权 IDs；每个 decision 是 1–4096 字符的字符串。内置 `StructuredProvider` 要求模型返回 `{"decision":"LABEL"}` 或 `{"decisions":["LABEL", ...]}`，校验后按原始顺序绑定 IDs；模型不生成 ID，也不能改变恢复范围。自定义 SDK 可用 `CallbackProvider` 包装；`DemoProvider` 仅供 fixture 使用。

## 工具 adapter

| 接口 | 约束 |
|---|---|
| `sites` | 唯一的不可变 `Site` handles，恢复域互不重叠 |
| `observe(site)` | 返回字符串，不修改状态 |
| `snapshot(site)` | 返回完整 before-image 字符串 |
| `apply(site, decision)` | 只修改该 site 的私有状态，不发布外部 effect |
| `restore(site, snapshot)` | 完整恢复该 site |
| `validate(site)` | 返回 `Verdict.ACCEPT / REJECT / UNKNOWN` |
| `final_validate()` | 用 bool 表示完整任务是否通过验证 |

参考实现为 `JsonMigrationAdapter`。它检查完整目标 JSON 及不应变化的字段，而不只检查能否解析。

共享状态或跨 site 依赖会破坏独立恢复条件：应扩大 site，或让局部检查返回 UNKNOWN，等待全局证据。私有 workspace 也不能被其他 writer 并发修改。

## 运行与结果

`Runtime.execute()` 是一次性、同步会话，返回 `RunResult`：`success`、`error`、`metrics` 与 `events`。失败时先检查 error/trace，不要发布私有 workspace；其中可能残留供诊断的部分修改。

所有 publication 都等待全局验证。SQLite sink 将相同 logical ID / payload 去重，不同 payload 则报冲突；这不保证远端请求 exactly-once。真实 POST 等 effect 必须另外设计幂等接收方、outbox 或受控代理，不能放进 `apply` 后期待自动撤销。

编译器只接受 README 中的有限 workflow，不是 Python 沙箱；provider、adapter 和 validator 是可信宿主代码。更多安全边界见 [SECURITY.md](../SECURITY.md)。

## 可选真实模型检查

默认测试使用脚本化模型与本地 HTTP server。真实检查需要显式设置变量，会加载模型或产生 API 用量：

```bash
NEX_TEST_LOCAL_MODEL=Qwen/Qwen3-1.7B pytest -q tests/test_live.py -k local
# 先按上文配置 NEX_BASE_URL / NEX_API_KEY，服务端需返回纯 JSON content：
NEX_TEST_API_MODEL="$NEX_MODEL" pytest -q tests/test_live.py -k api
```
