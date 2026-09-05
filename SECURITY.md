# 安全边界

NEX 是研究型参考实现，不是生产安全产品。

- 仅 `WorkflowCompiler` 文档中的小型 DSL 被允许执行。语法检查不是 OS 沙箱。
- adapter、validator、provider 是可信宿主代码，拥有当前 Python 进程的权限；不要把不可信模型源码放进这些回调执行。
- 没有进程、网络、文件系统或 CPU/内存隔离。运行未知输入前请自行部署容器/权限边界与资源限额。
- 私有工作区不能被其他 writer 并发修改，局部 certificate 必须保持有效。当前 backend 不做动态 taint，也不自动捕获跨 site 隐式读写。
- SQLite 只保证本地 publication sink 的事务与去重，不保证跨系统 exactly-once。
- API 后端会把所选 observation 发给配置的模型服务；请确认服务的数据使用政策。仅允许远端 HTTPS，禁用重定向，不自动重试，认证头不进入摘要或错误正文。
- 本地模型拒绝 remote code，使用 safetensors；这不能代替依赖审计、GPU 资源隔离或模型来源验证。
- 不要向 issue、trace、日志或示例提交密钥、私有源码、真实 observation、个人信息和服务访问凭据。密钥通过环境变量传入。

如发现安全问题，请使用仓库的 **Security → Report a vulnerability** 私下报告（若该入口不可用，先联系维护者建立私密渠道），不要公开可直接利用的敏感信息。维护者会按实际能力处理，不承诺响应 SLA 或生产补丁支持周期。
