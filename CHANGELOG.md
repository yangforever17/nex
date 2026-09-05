# 变更记录

## Unreleased

- 将 fixture、本地 Transformers 和 Chat Completions 接入同一决策协议与运行入口。
- 增加严格 JSON/授权范围检查、有界推理、API 错误脱敏和逐调用用量记录。
- 增加 HTTP 链路测试与显式启用的真实模型检查；双语文档按统一使用路径重组。

## 0.1.0

- 整理独立、标准库优先的 Python 包与 CLI。
- 发布受限 workflow 编译器、模型无关回调、保守未决窗口恢复与最终验证门控。
- 增加 SQLite 本地 publication 的持久化幂等和冲突检测。
- 增加真实私有 JSON 写入 demo、同安全门控的 Full Retry 对照、全局证据退化路径。
- 保留独立的 fan-out 诊断及可选 DAG / opaque-envelope 受控实验。
- 提供安装、接入、执行边界、安全说明、README 图与自动化测试。
