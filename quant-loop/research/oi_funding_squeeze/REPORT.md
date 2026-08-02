# OI × Funding 挤压因子（squeeze）事件研究

日期：2026-08-02 ｜ 代码：`research/oi_funding_squeeze/squeeze.py`（纯函数核心，测试 11 项全绿）、`run_analysis.py`
理念：OI 飙升 + funding 极端 = 拥挤杠杆盘 → 逆拥挤方向做挤压（"谁被困住了"持仓因子家族）。

## 最终结论：**不用继续（KILL）**

两条独立的 Kill 理由同时成立：

1. **样本严重不足**：任务指定的日网格 + 20 日 z-score 窗口下，有效因子天数 = **0**，事件数 = 0（要求 ≥30）。
2. **即便把窗口放宽到极限（小时级 5 日窗口）也只凑出 24 个重叠事件，且方向与挤压理论相反**：方向调整后 24h 均值 −0.10%（t=−0.33）、72h −0.00%（t=−0.01）、168h −0.29%（t=−0.59），胜率 33–46%，全部跑输同方向规则的无条件基线。

## 1. 数据覆盖（audit-by-replication，枚举 300 个数据文件后确认）

| 数据集 | 粒度 | 覆盖区间 | 备注 |
|---|---|---|---|
| `data/oi/{7标的}.parquet` | **小时级**（非日级） | 2026-07-12 → 2026-08-02（500 行/标的） | Binance openInterestHist 只保留最近窗口，无更久历史 |
| `data/funding/{7标的}.parquet` | 8h | 2021-11-20 → 2026-07-25 | BTC/ETH/SOL 在 OI 窗口内有缺行（23/42 条），BNB/AVAX/DOGE/LINK 完整 |
| `data/spot/{7标的}_1h.parquet` | 1h | 2021-11-01 → **2026-06-30** | **与 OI 零重叠，不可用** |
| `data/perp_30m/{7标的}_30m.parquet` | 30m | 2022-01-01 → 2026-07-24 | 本研究价格源（7 标的齐全） |
| `data/perp_1m/` | 1m | → 2026-07-24 | 仅 BTC/ETH/SOL，未用 |

**三方共同窗口：2026-07-12 → 2026-07-24 ≈ 12 天。** 这是全部可用样本。

关键事实：任务背景假设"OI 日级、可能只有 30–90 天"——实际是**小时级、只有 21 天**，且价格数据末端（7/24）比 OI 末端（8/02）早 9 天，共同窗口进一步压缩到 12 天。任何 ≥12 日的滚动窗口在日网格下都产不出一个有效值。

## 2. 因子定义（三个候选都实现并测试）

- a. `oi_z`：OI 日变化率的滚动 z-score（窗口 w，只用过去数据，无前视）
- b. `fund_mean`：8h funding 率 → 日均值
- c. `squeeze_score = oi_z × sign(fund_mean)`：OI 飙升+正 funding = 多头拥挤（做空候选，direction=−1）；OI 飙升+负 funding = 空头拥挤（做多候选，direction=+1）
- 事件：|squeeze_score| > 2；收益做方向调整（正值 = 挤压理论方向正确）；基线 = 同一方向规则在全部有效日/小时上的收益（分离极端度过滤的增量信息）

## 3. 事件研究结果

### SPEC（任务指定：日网格，20 日窗口）

7 个标的有效因子天数全部 = 0，事件 = 0。**无法构造因子，更无事件研究可言。**

### LOOSE5（宽松变体：日网格，5 日窗口）

有效因子天数合计 37 天（每标的 2–9 天），**|score|>2 事件 = 0**——12 天的样本里日级 OI 变化率从未达到 5 日窗口的 2σ。基线（17 个方向规则日）24h 均值 +0.49%/t=1.31，样本太小无意义。

### HOURLY5（极限变体：小时网格，120h 窗口，前向 24/72/168h）

警告：事件窗口互相重叠，t 值偏乐观；BTC/ETH/SOL 因 funding 缺行有效小时数仅 ~60。

| 组 | horizon | n | mean（方向调整） | t | 胜率 | 基线 mean | excess |
|---|---|---|---|---|---|---|---|
| pooled | 24h | 24 | −0.10% | −0.33 | 37.5% | +0.07% | −0.17% |
| pooled | 72h | 24 | −0.00% | −0.01 | 45.8% | +0.06% | −0.06% |
| pooled | 168h | 24 | −0.29% | −0.59 | 33.3% | +0.03% | −0.32% |

分标的 24h：AVAX n=15 mean=−0.14% t=−0.30；BNB n=2 t=+3.96（2 个事件无意义）；BTC/DOGE n=2 t=−1.00；SOL n=2 t=+0.33；LINK n=1；ETH n=0。

**方向与理论相反**：逆拥挤方向平均是亏的（三个 horizon 全负），且落后基线。在唯一勉强可用的变体里，挤压信号不但没有 edge，连方向都不对。

## 4. 逐年/分段稳定性

不适用——样本只有 12 天，无法分段（要求 ≥2 年）。

## 5. 备注与后续可能

- 根因是数据：Binance `openInterestHist` API 不提供更久历史，本次下载只能拿到最近 ~21 天。若要认真测这个因子家族，需要：(a) 从现在开始**持续落库** OI（每小时），攒 ≥1 年后重测；或 (b) 找第三方历史 OI（Coinalyze/Coinglass 等，付费）。
- funding 单独的长历史因子已由 `research/xs_funding` 覆盖（结论同样偏弱：binance-bybit pooled t<1.1）。
- 本目录代码可复用：数据攒够后跑 `python research/oi_funding_squeeze/run_analysis.py` 即可重测。

## 文件清单

- `research/oi_funding_squeeze/squeeze.py` — 纯函数核心（对齐/因子/事件研究/统计）
- `research/oi_funding_squeeze/tests/test_squeeze.py` — 11 项 pytest，全绿
- `research/oi_funding_squeeze/run_analysis.py` — 真实数据运行脚本（SPEC/LOOSE5/HOURLY5 三变体）
- `research/oi_funding_squeeze/analysis_output.txt` — 完整运行输出
- `research/oi_funding_squeeze/REPORT.md` — 本报告
