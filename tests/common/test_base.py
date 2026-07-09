"""M1 整改单测：`load_dotenv_if_present` 按已解析路径去重，自定义路径不再被忽略。

回归动机：旧实现用一个模块级布尔锁 `_ENV_LOADED`，第一次调用（不管传的是哪个
`dotenv_path`）之后，后续调用全部直接 return——自定义路径被静默忽略，且解析
`.env` 内容的循环在测试里 0 覆盖率。改为按路径去重后，不同路径各自独立生效。
"""

import os
from pathlib import Path

import pytest

from tally.common.config.base import load_dotenv_if_present, resolve_env_ref


def test_load_dotenv_if_present_loads_custom_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "custom.env"
    var_name = "TALLY_TEST_DOTENV_VAR"
    env_file.write_text(
        f'# 注释行\n{var_name}="hello world"\nBAD_LINE_WITHOUT_EQUALS\n\n', encoding="utf-8"
    )
    monkeypatch.delenv(var_name, raising=False)

    load_dotenv_if_present(env_file)

    assert os.environ[var_name] == "hello world"
    monkeypatch.delenv(var_name, raising=False)


def test_load_dotenv_if_present_does_not_override_existing_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "custom2.env"
    var_name = "TALLY_TEST_DOTENV_VAR_PRESET"
    env_file.write_text(f"{var_name}=from_dotenv\n", encoding="utf-8")
    monkeypatch.setenv(var_name, "from_real_environment")

    load_dotenv_if_present(env_file)

    assert os.environ[var_name] == "from_real_environment"


def test_load_dotenv_if_present_is_idempotent_per_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """对同一个路径重复调用不应重新解析文件内容（也不应报错）。"""
    env_file = tmp_path / "custom3.env"
    var_name = "TALLY_TEST_DOTENV_VAR_IDEMPOTENT"
    env_file.write_text(f"{var_name}=first\n", encoding="utf-8")
    monkeypatch.delenv(var_name, raising=False)

    load_dotenv_if_present(env_file)
    env_file.write_text(f"{var_name}=second\n", encoding="utf-8")  # 修改文件内容
    load_dotenv_if_present(env_file)  # 第二次调用应被去重，不重新读取

    assert os.environ[var_name] == "first"
    monkeypatch.delenv(var_name, raising=False)


def test_load_dotenv_if_present_missing_file_is_a_noop(tmp_path: Path) -> None:
    missing_path = tmp_path / "does-not-exist.env"
    load_dotenv_if_present(missing_path)  # 不应抛异常


def test_resolve_env_ref_passthrough_for_non_env_string() -> None:
    assert resolve_env_ref("plain-value") == "plain-value"
    assert resolve_env_ref(42) == 42
    assert resolve_env_ref(None) is None


def test_resolve_env_ref_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TALLY_TEST_RESOLVE_VAR", "resolved-value")
    assert resolve_env_ref("env:TALLY_TEST_RESOLVE_VAR") == "resolved-value"


def test_resolve_env_ref_missing_var_falls_back_to_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TALLY_TEST_RESOLVE_VAR_MISSING", raising=False)
    assert resolve_env_ref("env:TALLY_TEST_RESOLVE_VAR_MISSING") == ""
