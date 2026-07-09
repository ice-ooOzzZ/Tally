"""tally CLI 入口（typer app）。

M0.5 阶段仅骨架：注册 app 与 version 回调，保证 `tally --help` 可用。
具体子命令（run/fill/capital 等）在 M1 起按里程碑填入，见 IMPLEMENTATION_SPEC.md §11。
"""

from __future__ import annotations

import typer

from tally import __version__

app = typer.Typer(
    name="tally",
    help="Tally（明账）— 规则化选股与建议追踪系统。",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"tally {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="打印版本号后退出。",
    ),
) -> None:
    """Tally CLI 顶层入口。"""


if __name__ == "__main__":
    app()
