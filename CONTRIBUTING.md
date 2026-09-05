# 贡献指南

欢迎小而可验证的改动。先把 bug、输入、预期行为和实际行为说清楚；机制扩展应说明它如何影响 evidence、dependency、recovery 与 publication。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,experiments]'
ruff check .
pytest -q
python -m build
```

提交前请确认：

- 正向与负向路径都有测试；安全相关改动包含 fail-closed 测试。
- benchmark 清楚区分 deterministic replay 与真实模型测量，不用未测得的 speedup 做结论。
- 不引入绝对机器路径、凭据、模型权重、论文源文件、历史结果或第三方源码快照。
- 新功能的限制和运行步骤已写入中文文档，公开 API 与原行为兼容或清楚说明变更。
- CI 不需要 GPU、API key、私有网络或大型数据下载。

请勿提交密钥或敏感 trace。贡献默认依据本仓库 Apache-2.0 许可证；只提交你有权贡献的内容。

自动测试模板见 [.github/ci-template.yml](.github/ci-template.yml)。维护者需使用有 workflow 权限的凭据，将其移至 `.github/workflows/ci.yml` 后启用；当前模板本身不会触发 GitHub Actions。
