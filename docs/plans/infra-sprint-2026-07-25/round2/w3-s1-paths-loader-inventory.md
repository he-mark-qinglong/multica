# w3-s1 — W3 T1–T3 执行卡：paths.py / data_loader.py / 数据 inventory 生成器

> Round-2 细化，2026-07-25。3 张卡，全部已对本机真实代码/数据核实（file:line 引用均为实读）。
> 执行者：caocao-m3 cheap agent，零上下文，30 分钟预算。

---

## 0. 给所有执行 agent 的公共前置（每张卡都适用，执行时内联进 prompt）

- 工作目录：`/Users/mark/multica/quant-loop`（下称 `$QL`）。所有相对路径以此为基准。
- Python 解释器**必须**用 `/Users/mark/sdk/mamba-envs/trading/bin/python3`（系统 python3 没有 pyarrow/pandas/yaml）。已验证该环境有：pandas 2.2.3 / pyarrow 18.1.0 / pyyaml 6.0.2 / pytest 8.3.4。
- **禁止**任何 git 操作（add/commit/push/stash）。worktree 里有别人的未提交改动，只许碰你卡片里列出的文件。
- **禁止**运行任何策略回测。
- 只许新建/修改卡片「Writes」列出的文件；其余文件一律只读。
- 现有测试约定（照抄）：测试文件与被测模块同目录、命名 `test_<module>.py`，文件头用 sys.path 插入保证可直接 `pytest <path>` 运行。参考样本 `$QL/scripts/test_build_perp_resampled_manifest.py:1-16`：
  ```python
  _HERE = Path(__file__).resolve().parent
  if str(_HERE) not in sys.path:
      sys.path.insert(0, str(_HERE))
  ```
  `$QL` 根目录**没有** conftest.py / pytest.ini（已核实），pytest 直接跑单文件即可。
- 双模式 import 参考样本（`_shared` 模块被包方式和裸模块方式两种 import）：`$QL/_shared/execution/cost_model.py:36-52` 的 `_import_factor_backtester()`。
- 已存在的 `__file__` 根推导样本：`$QL/scripts/build_perp_resampled_manifest.py:57`：`QUANT_LOOP_ROOT = Path(__file__).resolve().parents[1]`。

## 0.1 已核实的真实数据布局（T2/T3 的 ground truth，直接照用）

| 数据集 | 路径模式 | 备注（实测） |
|---|---|---|
| klines 1m | `data/perp_1m/{SYM}_1m.parquet`，3 币 BTC/ETH/SOL | 12 列：`open_time,open,high,low,close,volume,close_time,quote_volume,trades,taker_buy_base,taker_buy_quote,ignore`；BTC 3,605,862 行；`open_time` 是**列**不是索引 |
| klines 5m/15m | `data/perp_{5m,15m}/{SYM}_{tf}.parquet`，3 币 | 10 列（无 `close_time`/`ignore`）；BTCUSDT_15m = **240,392 行**（`data/manifests/perp_resampled_2026-07-24.yaml:63-65`） |
| klines 30m | `data/perp_30m/{SYM}_30m.parquet`，**7 币**（+BNB/DOGE/AVAX/LINK） | 8 列：`open_time,open,high,low,close,volume,close_time,quote_volume` |
| klines 2h | `data/perp_2h/{SYM}_2h.parquet`，3 币 | schema 同 30m |
| funding | `data/funding/{SYM}.parquet`，7 币 | 4 列：`ts(timestamp[ms,UTC]),symbol,fundingRate,markPrice`（`data/funding/README.md`「Schema written」节）；BTC 5,100 行，first ts **2021-11-20T16:00:00Z**（实测），last 2026-07-17 |
| aggTrades | `data/trades/{SYM}_aggtrades.parquet`，7 币 | **是 hive-partitioned 目录**（`year=YYYY/month=M/data.parquet`），不是单文件！8 列：`ts(timestamp[ms,tz=UTC]),symbol,agg_id,price,qty,first_id,last_id,is_buyer_maker`；BTC 2026-01→2026-07 共 7 fragments；全量 9.8G，**禁止不带列裁剪/过滤全读** |
| features | `data/features/feature_matrix_{BTC,ETH}.parquet` | 仅 2 币 |
| tradfi 日线 | `data/tradfi_1d/{BTC-USD,QQQ,SPY}_1d.parquet` | — |
| vpvr | `data/vpvr/` | **空目录** |
| manifests | `data/manifests/` | `perp_resampled_2026-07-24.yaml`（唯一完整 hash 清单）+ `volatility_edge_2026-07-20.yaml` |

- `_shared/` 现状（已 `ls` 核实）：`execution/ gates/ indicators/ regime/ sizing/ templates/ validation/ validators/ run_backtest.py test_run_backtest.py __init__.py` —— **没有** `paths.py`、`data_loader.py`，`_shared/` 内 grep `os.environ` 零命中。
- 基线验证：`cd $QL && python3 -m pytest _shared/test_run_backtest.py -q` → `10 passed, 2 skipped`（改完 T1/T2 后重跑应保持不变）。

---
---

## 卡 T1 — `_shared/paths.py` 环境变量路径解析

- **Goal**：新建 `_shared/paths.py`，提供 `quant_loop_root()` / `data_root()` / `live_data_root()` 三个路径解析函数，`QUANT_LOOP_ROOT` 环境变量优先、缺省从 `__file__` 推导，零配置可用。
- **Reads**（只读参考，不改）：
  - `$QL/_shared/execution/cost_model.py:36-52`（双模式 import 风格参考）
  - `$QL/scripts/build_perp_resampled_manifest.py:57`（`__file__` 根推导参考）
- **Writes**（新建，均不存在，已核实）：
  - `$QL/_shared/paths.py`
  - `$QL/_shared/test_paths.py`
- **Est**：15 min ｜ **Machine**：either（纯代码，不碰数据）
- **Depends on**：无

### Steps

1. 新建 `_shared/paths.py`，内容要点：
   - 模块 docstring 说明：这是 quant-loop 内所有路径解析的唯一来源；env var 名 `QUANT_LOOP_ROOT` 指向 quant-loop/ 目录本身；未设置时从 `__file__` 推导（paths.py 位于 `quant-loop/_shared/`，`Path(__file__).resolve().parents[1]` 即 quant-loop/）。
   - 参考实现（可直接采用，允许微调命名但三个公开函数名必须如下）：
     ```python
     """Path resolution for quant-loop. Single source of truth.

     All code that needs repo/data paths MUST import from here instead of
     hardcoding absolute paths. Resolution order:
       1. $QUANT_LOOP_ROOT env var (points at the quant-loop/ directory)
       2. Derived from __file__ (this file lives in quant-loop/_shared/)
     """
     import os
     from pathlib import Path

     ENV_VAR = "QUANT_LOOP_ROOT"


     def quant_loop_root() -> Path:
         env = os.environ.get(ENV_VAR)
         if env:
             return Path(env).expanduser().resolve()
         return Path(__file__).resolve().parents[1]


     def data_root() -> Path:
         return quant_loop_root() / "data"


     def live_data_root() -> Path:
         return quant_loop_root() / "live_data"
     ```
   - 不要加 `if __name__ == "__main__"` CLI，保持最小。
2. 新建 `_shared/test_paths.py`（头部 sys.path 处理照 §0 的样本模式，使 `import paths` 裸模块可用），覆盖：
   - 无 env 时 `quant_loop_root()` 等于 `Path(paths.__file__).resolve().parents[1]` 且该目录下存在 `_shared/` 子目录。
   - 无 env 时 `data_root() == quant_loop_root() / "data"`、`live_data_root() == quant_loop_root() / "live_data"`。
   - 用 `monkeypatch.setenv("QUANT_LOOP_ROOT", "/tmp/ql_fake")` 后 `quant_loop_root() == Path("/tmp/ql_fake")`（env 优先，**不要求目录存在**）。
   - 用 `monkeypatch.delenv("QUANT_LOOP_ROOT", raising=False)` 验证回落到 `__file__` 推导。
3. 跑验收（下节）。再跑 `_shared/test_run_backtest.py` 确认未破坏既有测试。

### Acceptance

```bash
cd /Users/mark/multica/quant-loop
/Users/mark/sdk/mamba-envs/trading/bin/python3 -m pytest _shared/test_paths.py -q        # 全过（≥4 个 test）
/Users/mark/sdk/mamba-envs/trading/bin/python3 -m pytest _shared/test_run_backtest.py -q # 仍 10 passed, 2 skipped
QUANT_LOOP_ROOT=/tmp/x /Users/mark/sdk/mamba-envs/trading/bin/python3 -c "import sys; sys.path.insert(0,'_shared'); from paths import data_root; print(data_root())"   # 输出 /tmp/x/data
/Users/mark/sdk/mamba-envs/trading/bin/python3 -c "import sys; sys.path.insert(0,'_shared'); from paths import quant_loop_root; print(quant_loop_root())"            # 输出 /Users/mark/multica/quant-loop
```

---
---

## 卡 T2 — `_shared/data_loader.py` 统一数据加载器

- **Goal**：新建 `_shared/data_loader.py`，按 §0.1 的真实数据布局提供 `load_bars(symbol, tf)` / `load_funding(symbol)` / `load_aggtrades(symbol, start, end, columns=None)` / `available(symbol)` 四个函数，全部走 T1 的 `_shared/paths.py`；附测试（合成 parquet + 真实数据只读冒烟）。
- **Reads**（只读参考，不改）：
  - `$QL/_shared/paths.py`（T1 产出；若同批并行 T1 未完成，先等 T1——本卡硬依赖它）
  - `$QL/data/manifests/perp_resampled_2026-07-24.yaml:63-65`（BTCUSDT_15m rows=240392 验收锚点）
  - `$QL/_shared/templates/run_strategy.py:82-97`（`load_bars_dir` 只认 `{SYMBOL}.parquet` 平铺命名，与真实布局 `{SYM}_{tf}.parquet` 不符——**只把这个差异写进 data_loader.py 的模块 docstring，绝不改 run_strategy.py**，该文件归另一张卡所有）
  - `$QL/data/funding/README.md`（funding schema）
- **Writes**（新建，均不存在，已核实）：
  - `$QL/_shared/data_loader.py`
  - `$QL/_shared/test_data_loader.py`
- **Est**：25 min ｜ **Machine**：**mac**（验收要读真实 `data/perp_15m/BTCUSDT_15m.parquet`）
- **Depends on**：**T1**

### Steps

1. 新建 `_shared/data_loader.py`。模块 docstring 写清：
   - 支持的数据集与路径约定（照抄 §0.1 表格核心行：klines `data/perp_{tf}/{SYM}_{tf}.parquet`，tf ∈ `1m,5m,15m,30m,2h`；funding `data/funding/{SYM}.parquet`；aggTrades `data/trades/{SYM}_aggtrades.parquet` 为 hive-partitioned **目录**）。
   - 已知局限备注：`_shared/templates/run_strategy.py` 的 `load_bars_dir`（该文件 82-97 行）期望平铺 `{SYMBOL}.parquet`，与本加载器的 `{SYM}_{tf}.parquet` 约定不同；本模块是新策略的权威入口，run_strategy 的适配由后续任务处理。
   - 已知数据缺口（写一句话即可）：funding 仅 2021-11-20 起；aggTrades 仅 2026 年；30m 是唯一覆盖 7 币的 klines 周期。
2. import 模式：文件头部照 `cost_model.py:36-52` 的思路做双模式——先 `from _shared.paths import data_root`，`ImportError` 时把 `Path(__file__).resolve().parents[1]`（quant-loop/）插入 `sys.path` 再 import。这样策略目录里 `sys.path.insert(0, '.../_shared')` 后 `import data_loader` 也能用。
3. 实现（关键骨架，可直接采用）：
   ```python
   BAR_TFS = ("1m", "5m", "15m", "30m", "2h")

   def load_bars(symbol: str, tf: str, start=None, end=None) -> pd.DataFrame:
       """Load klines for symbol/tf. Returns DF indexed by UTC DatetimeIndex
       (from the open_time column), sorted ascending."""
       if tf not in BAR_TFS:
           raise ValueError(f"unknown tf {tf!r}; expected one of {BAR_TFS}")
       path = data_root() / f"perp_{tf}" / f"{symbol}_{tf}.parquet"
       if not path.is_file():
           raise FileNotFoundError(f"no klines for {symbol} {tf}: {path}")
       df = pd.read_parquet(path)
       df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
       df = df.set_index("open_time").sort_index()
       if start is not None:
           df = df[df.index >= pd.Timestamp(start, tz="UTC")]
       if end is not None:
           df = df[df.index < pd.Timestamp(end, tz="UTC")]
       return df
   ```
   注意：不同 tf 列数不同（1m 12 列含 `close_time`/`ignore`，5m/15m 10 列，30m/2h 8 列，见 §0.1）——**不要**丢弃多余列，原样保留，只统一索引。`start`/`end` 接受 str 或 Timestamp，end 为排他。
   ```python
   def load_funding(symbol: str, start=None, end=None) -> pd.DataFrame:
       """Load 8h funding. ts column -> UTC DatetimeIndex."""
       path = data_root() / "funding" / f"{symbol}.parquet"
       # 同样的 FileNotFoundError / 索引化 / 时间过滤模式
   ```
   ```python
   def load_aggtrades(symbol, start, end, columns=None) -> pd.DataFrame:
       """Load aggTrades slice. The parquet path is a hive-partitioned
       DIRECTORY (year=/month=); MUST use pyarrow.dataset with column
       projection + ts filter — never read the whole dataset."""
       import pyarrow.dataset as ds
       path = data_root() / "trades" / f"{symbol}_aggtrades.parquet"
       if not path.is_dir():
           raise FileNotFoundError(...)
       dataset = ds.dataset(str(path), format="parquet", partitioning="hive")
       start_ts = pd.Timestamp(start, tz="UTC")
       end_ts = pd.Timestamp(end, tz="UTC")
       filt = (ds.field("ts") >= start_ts) & (ds.field("ts") < end_ts)
       table = dataset.to_table(columns=columns, filter=filt)
       return table.to_pandas()
   ```
   `start`/`end` 为必填（防止误全量扫 9.8G）；ts 是 `timestamp[ms, tz=UTC]`，pyarrow 过滤直接可用。
   ```python
   def available(symbol: str) -> dict:
       """Coverage report, e.g.
       {'bars': ['1m','5m','15m','30m','2h'], 'funding': True, 'aggtrades': True}
       —只查文件存在性，不读数据。"""
   ```
4. 新建 `_shared/test_data_loader.py`（头部 sys.path 处理照 §0 样本）：
   - **合成数据测试**：`tmp_path` 下造 `data/perp_15m/BTCUSDT_15m.parquet`（几十行，`open_time` 为 ms 或 datetime 均可——用 `pd.to_datetime(..., utc=True)` 兼容）、`data/funding/BTCUSDT.parquet`、`data/trades/BTCUSDT_aggtrades.parquet/`（用 `ds.write_dataset(..., partitioning=ds.partitioning(schema, flavor='hive')` 或直接 `pq.write_table` 到 `year=2026/month=1/data.parquet` 子目录），`monkeypatch.setenv("QUANT_LOOP_ROOT", str(tmp_path))` 后测：load_bars 索引类型/排序/start-end 过滤、load_funding、load_aggtrades 列裁剪（`columns=["ts","price"]` 结果只有 2 列）、unknown tf 抛 ValueError、缺文件抛 FileNotFoundError、available() 返回结构。
   - **真实数据冒烟**（只读，标注 `@pytest.mark.skipif(not (data_root()/"perp_15m/BTCUSDT_15m.parquet").is_file(), reason="no real data")`，注意 skipif 在 import 时求值，要确保此时未设 QUANT_LOOP_ROOT 或用默认推导）：用 pyarrow 直接 `pq.read_table(path, columns=["open_time"]).slice(0, 100)` 验证可读 + 行数元数据 `pq.ParquetFile(path).metadata.num_rows == 240392`。**不要**在测试里全量 load 1m/trades。
5. 跑验收。

### Acceptance

```bash
cd /Users/mark/multica/quant-loop
/Users/mark/sdk/mamba-envs/trading/bin/python3 -m pytest _shared/test_data_loader.py -q   # 全过
/Users/mark/sdk/mamba-envs/trading/bin/python3 -m pytest _shared/test_paths.py _shared/test_run_backtest.py -q  # 不回归
/Users/mark/sdk/mamba-envs/trading/bin/python3 -c "
from _shared.data_loader import load_bars
df = load_bars('BTCUSDT', '15m')
assert len(df) == 240392, len(df)
assert df.index.is_monotonic_increasing and str(df.index.tz) == 'UTC'
print('OK', len(df), df.index[0], df.index[-1])"
# 期望: OK 240392 2019-09-08 17:45:00+00:00 2026-07-17 19:30:00+00:00（锚点: manifest:64-69）
```

---
---

## 卡 T3 — 数据 inventory 生成器（`scripts/build_data_inventory.py`）

- **Goal**：新建 `$QL/scripts/build_data_inventory.py`，扫描 `data/` 生成两份提交物：(a) `data/README.md` 人读覆盖矩阵；(b) `data/manifests/inventory.yaml` 机读清单（每数据集×币种×行数×首末时间），支持 `--check` 幂等校验。只写这 3 个文件，**不改任何 parquet/既有 manifest**。
- **Reads**（只读参考，不改）：
  - `$QL/_shared/paths.py`（T1 产出，硬依赖——路径解析只许 import 它）
  - §0.1 数据布局表（本卡 ground truth）
  - `$QL/scripts/build_perp_resampled_manifest.py:57` 及 `:1-40`（既有 manifest 生成器的 docstring/结构风格参考）
  - `$QL/data/manifests/perp_resampled_2026-07-24.yaml`（yaml 字段风格参考；本卡产物是新文件 `inventory.yaml`，不动它）
- **Writes**（前两个新建已核实不存在；第三个新建）：
  - `$QL/scripts/build_data_inventory.py`
  - `$QL/data/README.md`
  - `$QL/data/manifests/inventory.yaml`
- **Est**：25 min ｜ **Machine**：**mac**（要扫真实 `data/`）
- **Depends on**：**T1**

### Steps

1. 新建 `scripts/build_data_inventory.py`。头部 docstring 写清 inputs(read-only)/outputs/acceptance（风格照 `build_perp_resampled_manifest.py:1-40`）。路径解析：`from _shared.paths import data_root`（import 前按 §0 样本把 `Path(__file__).resolve().parents[1]` 即 quant-loop/ 插 sys.path，使脚本可 `python3 scripts/build_data_inventory.py` 直接跑）。**禁止**在脚本里写任何绝对路径。
2. 扫描逻辑（全部用 pyarrow 元数据/单列读取，保证秒级完成；**禁止** `pd.read_parquet` 全读 trades）：
   - klines：对 `data/perp_{1m,5m,15m,30m,2h}/` 每个 `{SYM}_{tf}.parquet`，用 `pq.ParquetFile(p).metadata.num_rows` 取行数；first/last 时间用 `pq.read_table(p, columns=["open_time"])` 取 min/max（klines 最大 3.6M 行单列，可接受）。
   - funding：`data/funding/*.parquet`（忽略 `.csv`/`.json`/`fetch_funding.py`），行数同上，first/last 读 `ts` 列。
   - trades：`data/trades/{SYM}_aggtrades.parquet` 是目录——`ds.dataset(path, format="parquet", partitioning="hive")`；行数 = 各 fragment `ParquetFragment.metadata.num_rows` 求和（零读取）；first/last ts = 按 path 排序后**只读第一个 fragment 的 ts min 与最后一个 fragment 的 ts max**（`dataset.to_table` 加 filter 或对该 fragment 文件 `pq.read_table(columns=["ts"])`）。
   - features / tradfi_1d：同 klines 模式，时间列：features 若无明显时间列则只记行数并在 yaml 里 `time_col: null`；tradfi_1d 用其首列 datetime 列（实现时用 `pd.read_parquet` 读 1 行探测列名，小文件可全读）。
   - `data/vpvr/`：空目录，yaml 里记 `status: empty`。
   - 已知缺口写进 README 的「Known gaps」固定段落（文字照抄）：funding 仅 2021-11-20→2026-07-17（缺 2019-2021）；aggTrades 仅 2026-01→2026-07；30m 是唯一覆盖 7 币的 klines 周期；features 仅 BTC/ETH；vpvr 空。
3. 输出格式：
   - `data/manifests/inventory.yaml`：`yaml.safe_dump(..., sort_keys=True)`，顶层结构 `datasets: {perp_1m: {path_pattern, symbols: {BTCUSDT: {path, rows, first_ts, last_ts}, ...}}, funding: {...}, trades: {...}, features: {...}, tradfi_1d: {...}, vpvr: {status: empty}}`；时间一律 ISO8601 UTC 字符串。**禁止写入任何 wall-clock 生成时间戳**（否则 --check 永不幂等）；可以写静态字段 `generator: scripts/build_data_inventory.py`。
   - `data/README.md`：固定标题结构 + 覆盖矩阵 markdown 表（数据集 × 币种 × 行数 × 起止）+ Known gaps 段。内容全部由扫描结果确定性生成。
4. CLI：`argparse`，默认动作为重新生成两个文件；`--check` 时在内存中生成并与磁盘内容逐字节比较，一致打印 `inventory up to date` 退出 0，不一致打印哪个文件 stale 退出 1。
5. 先跑 `python3 scripts/build_data_inventory.py` 生成两份产物，再跑 `--check` 验证幂等。**人工核对**生成的 inventory.yaml 中以下锚点（已在 planning 阶段实测，若不符说明扫描逻辑有 bug，修到符为止）：
   - `funding.BTCUSDT.rows == 5100`，`first_ts` 以 `2021-11-20` 开头
   - `perp_15m.BTCUSDT.rows == 240392`，`first_ts` 以 `2019-09-08` 开头
   - `perp_1m.BTCUSDT.rows == 3605862`
   - `perp_30m.symbols` 含 7 个币（BTC/ETH/SOL/BNB/DOGE/AVAX/LINK）
   - `vpvr.status == empty`

### Acceptance

```bash
cd /Users/mark/multica/quant-loop
/Users/mark/sdk/mamba-envs/trading/bin/python3 scripts/build_data_inventory.py --check   # 退出码 0，输出 "inventory up to date"（先无 --check 跑一遍再 check）
/Users/mark/sdk/mamba-envs/trading/bin/python3 -c "
import yaml
inv = yaml.safe_load(open('data/manifests/inventory.yaml'))
assert inv['datasets']['funding']['symbols']['BTCUSDT']['first_ts'].startswith('2021-11-20')
assert inv['datasets']['perp_15m']['symbols']['BTCUSDT']['rows'] == 240392
assert len(inv['datasets']['perp_30m']['symbols']) == 7
print('inventory OK')"
grep -q 'Known gaps' data/README.md && echo README OK
# 幂等性：连跑两次 --check 都退出 0；且未触碰任何 parquet：
git status --porcelain data/ | grep -v 'README.md\|inventory.yaml' | wc -l   # == 0（git status 只读，允许）
```

---

## 跨卡/跨 slice 备注（给 parent 与执行调度）

- T2、T3 硬依赖 T1（import `_shared/paths.py`），T1 必须先合入；T2 与 T3 文件不相交可并行。
- T2 被要求**只读** `_shared/templates/run_strategy.py`（命名差异只写进 docstring），该文件的修改归 W3 T13 卡——避免双写冲突。
- T3 的产物 `data/manifests/inventory.yaml` / `data/README.md` 后续会被 W3 T15（数据新鲜度刷新）重跑更新——那是预期内的依赖，不是冲突。
- 本 slice 三卡与 W3 Phase B 迁移 shards（T4-T9）零文件交叠（它们改策略/scripts 既有文件，本 slice 全新建）；T4-T9 的执行 prompt 里会 import T1 的 paths.py，所以 T1 是全局关键路径。
