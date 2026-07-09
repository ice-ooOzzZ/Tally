"""T0.5.5 单测：六形态固定种子可复现 + 极端涨跌幅注入 + 涨跌停约束 + 事件日标注。"""

import pandas as pd
import pytest

from tests.synth.generators import ExtremeMove, Shape, SynthConfig, generate_synthetic_kline

ALL_SHAPES: tuple[Shape, ...] = (
    "uptrend",
    "downtrend",
    "sideways",
    "breakout",
    "crash",
    "recover",
)


# ---- 固定种子可复现 --------------------------------------------------------------


@pytest.mark.parametrize("shape", ALL_SHAPES)
def test_shape_reproducible_with_fixed_seed(shape: Shape) -> None:
    cfg = SynthConfig(shape=shape, n_days=120, seed=7)
    df1 = generate_synthetic_kline(cfg)
    df2 = generate_synthetic_kline(cfg)
    pd.testing.assert_frame_equal(df1, df2)


@pytest.mark.parametrize("shape", ALL_SHAPES)
def test_shape_different_seed_differs(shape: Shape) -> None:
    cfg_a = SynthConfig(shape=shape, n_days=120, seed=1)
    cfg_b = SynthConfig(shape=shape, n_days=120, seed=2)
    df_a = generate_synthetic_kline(cfg_a)
    df_b = generate_synthetic_kline(cfg_b)
    assert not df_a["close"].equals(df_b["close"])


def test_generator_does_not_mutate_config() -> None:
    moves = (ExtremeMove(day_index=5, pct_change=-0.2),)
    cfg = SynthConfig(shape="uptrend", n_days=50, seed=3, extreme_moves=moves)
    generate_synthetic_kline(cfg)
    assert cfg.extreme_moves == moves  # 未被修改


# ---- M4 回归：crash/recover 在极小 n_days 下不应 negative-dimensions 崩溃 ----------


@pytest.mark.parametrize("shape", ALL_SHAPES)
@pytest.mark.parametrize("n_days", [1, 2, 3, 5, 8])
def test_all_shapes_handle_small_n_days_without_crashing(shape: Shape, n_days: int) -> None:
    """回归 M0.5 审查发现的 bug：crash/recover 形态在 n_days 很小时，某一段的
    size 会算成负数，触发 `ValueError: negative dimensions`（且并非期望中的
    "越界报错"，而是 numpy 内部报错）。修复后应始终成功钳制到 [0, n_days]。
    """
    df = generate_synthetic_kline(SynthConfig(shape=shape, n_days=n_days, seed=1))
    assert len(df) == n_days
    assert (df["close"] > 0).all()


def test_crash_and_recover_still_reproducible_at_tiny_n_days() -> None:
    for shape in ("crash", "recover"):
        cfg = SynthConfig(shape=shape, n_days=3, seed=9)
        df1 = generate_synthetic_kline(cfg)
        df2 = generate_synthetic_kline(cfg)
        pd.testing.assert_frame_equal(df1, df2)


def test_all_shapes_produce_expected_row_count_and_columns() -> None:
    for shape in ALL_SHAPES:
        df = generate_synthetic_kline(SynthConfig(shape=shape, n_days=64, seed=42))
        assert len(df) == 64
        expected_columns = ["date", "open", "high", "low", "close", "volume", "is_event_day"]
        assert list(df.columns) == expected_columns
        assert (df["high"] >= df["low"]).all()
        assert (df["close"] > 0).all()


# ---- 形态方向性合理性（粗粒度，非精确回测断言）------------------------------------


def test_uptrend_ends_higher_than_start() -> None:
    df = generate_synthetic_kline(SynthConfig(shape="uptrend", n_days=250, seed=42))
    assert df["close"].iloc[-1] > df["close"].iloc[0]


def test_downtrend_ends_lower_than_start() -> None:
    df = generate_synthetic_kline(SynthConfig(shape="downtrend", n_days=250, seed=42))
    assert df["close"].iloc[-1] < df["close"].iloc[0]


def test_crash_contains_a_large_drawdown() -> None:
    df = generate_synthetic_kline(SynthConfig(shape="crash", n_days=250, seed=42))
    running_max = df["close"].cummax()
    drawdown = (df["close"] / running_max - 1.0).min()
    assert drawdown < -0.20


def test_recover_ends_up_from_its_own_trough() -> None:
    df = generate_synthetic_kline(SynthConfig(shape="recover", n_days=250, seed=42))
    trough_idx = int(df["close"].idxmin())
    assert df["close"].iloc[-1] > df["close"].iloc[trough_idx]


# ---- 单日极端涨跌幅注入 + 涨跌停约束 -----------------------------------------------


def test_extreme_move_is_clipped_under_price_limit() -> None:
    """CN 模式(有涨跌停)：注入 -30% 应被裁剪到 -limit_pct。"""
    moves = (ExtremeMove(day_index=10, pct_change=-0.30),)
    cfg = SynthConfig(
        shape="sideways",
        n_days=50,
        seed=1,
        apply_price_limit=True,
        limit_pct=0.10,
        extreme_moves=moves,
    )
    df = generate_synthetic_kline(cfg)
    actual_return = df["close"].iloc[10] / df["close"].iloc[9] - 1.0
    assert actual_return == pytest.approx(-0.10, abs=1e-9)


def test_extreme_move_passes_through_without_price_limit() -> None:
    """美股模式(无涨跌停约束)：注入的 -30% 应原样保留。"""
    moves = (ExtremeMove(day_index=10, pct_change=-0.30),)
    cfg = SynthConfig(
        shape="sideways",
        n_days=50,
        seed=1,
        apply_price_limit=False,
        extreme_moves=moves,
    )
    df = generate_synthetic_kline(cfg)
    actual_return = df["close"].iloc[10] / df["close"].iloc[9] - 1.0
    assert actual_return == pytest.approx(-0.30, abs=1e-9)


def test_extreme_move_out_of_range_raises() -> None:
    cfg = SynthConfig(shape="uptrend", n_days=10, seed=1, extreme_moves=(ExtremeMove(20, -0.1),))
    with pytest.raises(ValueError, match="day_index"):
        generate_synthetic_kline(cfg)


# ---- 伴随事件日标注序列 -----------------------------------------------------------


def test_event_day_flagged_and_volume_boosted() -> None:
    cfg = SynthConfig(shape="sideways", n_days=60, seed=5, event_day_indices=(20, 40))
    df = generate_synthetic_kline(cfg)
    assert df["is_event_day"].tolist().count(True) == 2
    assert df["is_event_day"].iloc[20] and df["is_event_day"].iloc[40]

    baseline_cfg = SynthConfig(shape="sideways", n_days=60, seed=5, event_day_indices=())
    baseline_df = generate_synthetic_kline(baseline_cfg)
    assert df["volume"].iloc[20] > baseline_df["volume"].iloc[20]


def test_event_day_out_of_range_raises() -> None:
    cfg = SynthConfig(shape="uptrend", n_days=10, seed=1, event_day_indices=(99,))
    with pytest.raises(ValueError, match="event_day_index"):
        generate_synthetic_kline(cfg)
