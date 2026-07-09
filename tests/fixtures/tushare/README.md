# Tushare fixtures（录制回放，CLAUDE.md「fixture 录制回放」）

本目录下的 `daily.json` / `adj_factor.json` / `daily_basic.json` 是
`tally.data.sources.tushare_source.TushareSource` 对应三个 Tushare Pro 接口
（`daily` / `adj_factor` / `daily_basic`）的响应 fixture，供
`tests/data/test_tushare_source.py` 离线回放用（不联网、不需要 `TUSHARE_TOKEN`）。

## 格式

每个文件是一个 JSON **数组**，数组元素是该接口原始返回的一行记录（字段名与真实
Tushare Pro 返回的 DataFrame 列名逐一对应，未做任何归一化/改名），等价于
`df.to_dict(orient="records")` 的输出：

- `daily.json`：`ts_code`/`trade_date`/`open`/`high`/`low`/`close`/`pre_close`/
  `change`/`pct_chg`/`vol`/`amount`
- `adj_factor.json`：`ts_code`/`trade_date`/`adj_factor`
- `daily_basic.json`：`ts_code`/`trade_date`/`close`/`turnover_rate`/`pe`/
  `pe_ttm`/`pb`/`total_mv`/`circ_mv`

`tests/data/tushare_fixtures.py` 里的 `ReplayTushareTransport` 按需加载这些文件，
并模拟真实 `pro_api` 的过滤语义（`ts_code` 精确匹配、`trade_date` 精确匹配、
`start_date`/`end_date` 按 `YYYYMMDD` 字符串区间过滤，均可省略）。

## 现有样本的设计意图

- 覆盖两只代码（`600000.SH`、`000001.SZ`）× 三个交易日（2024-01-02/03/04）。
- `600000.SH` 在 01-03 → 01-04 之间模拟一次除权事件：`adj_factor` 从
  `1.2000` 跳到 `1.2500`，同时 01-04 的 `open`/`close` 相对 01-03 出现除权式下修——
  用于覆盖任务 AC「至少一个跨除权日/含复权因子变化的样本」。
- `000001.SZ` 三日 `adj_factor` 恒为 `1.0500`（无事件对照组），用于确认merge逻辑
  在无事件时不会误改 adj_factor。

## 现状与后续

**当前这三个文件是手工构造的（非真实 Tushare 响应）**——本任务开发时环境没有可用
的 `TUSHARE_TOKEN`。数值内部自洽（价格/成交量/市值量级合理、除权前后价格变动方向
正确）但不是真实市场数据。

**待有真实 token 时的录制流程**（见 `tests/data/tushare_fixtures.py` 的
`RecordingTushareTransport`）：

```python
import tushare as ts
from tests.data.tushare_fixtures import RecordingTushareTransport

real = ts.pro_api(token="<真实 token>")
recorder = RecordingTushareTransport(real)
recorder.daily(ts_code="600000.SH", start_date="20240101", end_date="20240110")
recorder.adj_factor(ts_code="600000.SH", start_date="20240101", end_date="20240110")
recorder.daily_basic(ts_code="600000.SH", start_date="20240101", end_date="20240110")
recorder.save(Path("tests/fixtures/tushare"))  # 覆盖写回本目录，替换手工样本
```

录制后请在对应 PR 里说明重新录制的原因（CLAUDE.md「fixture 录制回放」第 3 条）。
