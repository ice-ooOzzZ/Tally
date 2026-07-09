"""配置子系统的公共基础：仓库路径、`.env` 加载、`env:VAR` 引用解析、严格 BaseModel。

对应 IMPLEMENTATION_SPEC.md §0/§10。密钥一律走 `.env`（不入库），
config/*.yaml 中以 `env:VAR` 语义引用；本模块负责在加载配置时把该占位符
替换为环境变量的真实值。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

Market = Literal["CN", "US"]

REPO_ROOT: Path = Path(__file__).resolve().parents[4]
CONFIG_DIR: Path = REPO_ROOT / "config"

_ENV_LOADED = False


def load_dotenv_if_present(dotenv_path: Path | None = None) -> None:
    """极简 `.env` 加载器：只在对应环境变量尚未设置时才写入 os.environ。

    不依赖 python-dotenv（未列入 IMPLEMENTATION_SPEC.md §0 技术栈），仅支持
    `KEY=VALUE` 与 `# 注释` 两种行；重复调用是幂等的。
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    path = dotenv_path or (REPO_ROOT / ".env")
    if path.is_file():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
    _ENV_LOADED = True


def resolve_env_ref(value: object) -> object:
    """把形如 `env:VAR_NAME` 的字符串解析为环境变量的值；其他值原样返回。

    找不到对应环境变量时返回空字符串（脚手架/测试阶段允许密钥缺失），
    真正使用密钥的调用方（data/sources 等）负责在使用前校验非空。
    """
    if isinstance(value, str) and value.startswith("env:"):
        load_dotenv_if_present()
        var_name = value.removeprefix("env:")
        return os.environ.get(var_name, "")
    return value


class StrictModel(BaseModel):
    """项目内配置模型的公共基类：禁止未知字段（捕 typo）+ 校验后不可变。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
