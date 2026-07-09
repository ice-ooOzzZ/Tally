"""M0.5 T0.5.1 smoke test：确认包可导入、CLI app 可用（AC：`pytest` 空跑绿）。"""

from typer.testing import CliRunner

from tally import __version__
from tally.cli import app


def test_package_importable() -> None:
    assert __version__ == "0.1.0"


def test_cli_help_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "tally" in result.stdout.lower()


def test_cli_version_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
