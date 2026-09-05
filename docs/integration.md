# 接入指南

先运行 `python examples/run_migration.py`，确认环境正常，再替换其中的 provider 与 adapter。

## 模型回调

```python
predict(observations: tuple[str, ...], question: str) -> str
repair(request: RepairRequest) -> dict[str, str]
```

`RepairRequest` 包含 `site_ids`、对应的原始 `observations` 和 `reason`。修复字典必须恰好覆盖授权 IDs；每个 decision 是 1–4096 字符的字符串。SDK、模型服务、超时和 API key 由你的回调管理。默认 `DemoProvider` 是确定性替身，不调用真实 LLM。

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
