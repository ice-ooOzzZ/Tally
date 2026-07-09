# Tally（明账）

规则化选股与建议追踪系统。唯一实现依据见 [`IMPLEMENTATION_SPEC.md`](./IMPLEMENTATION_SPEC.md)；
开发流程见 [`docs/DEVELOPMENT_PROCESS.md`](./docs/DEVELOPMENT_PROCESS.md)；
项目铁律与目录约定见 [`CLAUDE.md`](./CLAUDE.md)。

## 快速开始

```bash
uv python install 3.12
uv venv --python 3.12
uv sync --extra dev --extra dashboard
cp .env.example .env   # 填入真实密钥，.env 不入库
uv run pytest
```
