# 低费率路径调研报告（2025–2026 最新）

调研日期：2026-08-02 · 账户规模假设：~$1M 本金 · 目标：maker ≤1.4bp，理想 ~0
来源：explore agent 全网调研（WebSearch + 官方文档），由主代理落盘。

## TL;DR 结论

- **首选路径：Lighter Standard 账户 —— maker 0bp / taker 0bp，无任何门槛**，官方文档确认。代价是人为延迟（maker 200ms / taker 300ms）。全市场唯一"零费率+零门槛"的 CLOB 永续通道。
- **备选 1（CEX 合规路径）：Binance VIP3 —— 2026 年 3 月门槛大降后对本账户现实可达**：$50M/30d 期货量（杠杆下可行）或 **Holder 通道 $1M 资产 + 100 BNB（2026 年 7 月新政，零交易量要求）** → maker 1.2bp（BNB 抵扣后 1.08bp），已优于 1.4bp 目标。
- **备选 2：dYdX，$25M/30d → maker 0bp**；$5M → maker 0.5bp。DEX、无 KYC、30 天窗口。
- **可叠加项**：sub-broker 返佣（最高 40%，对正 maker 费生效）可把 Binance VIP3 有效 maker 压到 ~0.65bp。
- **不现实项**：Binance MM Program（门槛 30d >1000 BTC ≈ $100M+ 月量）、OKX ELP（VIP7+）、Hyperliquid maker rebate 梯队（需平台 maker 份额 >0.5%）。对本账户如实说：这些短期无路径。
- **预期可达到的 maker 费率：0bp（Lighter/dYdX）~ 1.1bp（Binance VIP3），叠加返佣后 CEX 有效 ~0.65bp。**

---

## 1. Binance VIP 升级（2026 年门槛已大降）

来源：Datawallet — Binance VIP Levels Explained（含 2026 年 3 月/7 月两轮新政）

| 等级 | 30d 期货量 | BNB 日均 | maker / taker | BNB 9折后 maker |
|---|---|---|---|---|
| VIP0 | — | — | 2.0bp / 5.0bp | 1.8bp |
| VIP1 | **$5M**（原 $15M） | 5（原 25） | 1.6bp / 4.2bp | 1.44bp |
| VIP2 | **$10M**（原 $50M） | 25（原 100） | 1.4bp / 3.8bp | 1.26bp |
| VIP3 | **$50M**（原 $100M） | 100（原 250） | 1.2bp / 3.2bp | **1.08bp** |
| VIP9 | $4B 现货 | 5500 | 0 / 1.7bp | 0 |

**不需要刷量的三条旁路**：
- **Holder 通道**：VIP1 = $100k 平均资产 + 5 BNB；VIP2 = $200k + 25 BNB；**VIP3 = $1M 平均资产 + 100 BNB（2026-07 刚从 $3M 降到 $1M），完全不需要交易量**。
- **借款通道**：$100k 平均净借款 + 5 BNB → VIP1。
- **VIP Invitation Program**：用其他交易所 30d 量证明，直接给"高一级"费率 2 个月，期间免 BNB。

**$1M 本金做量结构**：$50M/30d = 日均 $1.67M 名义 = 每日仅 1.67 倍本金换手。3–5x 杠杆下任何中高频策略自然达标，**不需要 wash trading**。VIP1/VIP2（$5M/$10M）几乎自动达成。

**Wash trading 合规风险**：Binance 条款明确禁止，违者取消资格封禁；自成交有 STP 机制且不计有效量。结论：走真实策略量或 Holder 通道，零合规风险。

## 2. Binance 市场做市商计划（MM Program）—— 对本账户不现实

- 公布门槛：**30d 量 > 1,000 BTC**（≈$100M+ 月量）+ "优质做市策略"，邮件申请。
- 待遇：选定交易对负 maker 费，按做市表现滚动重定价，有报价义务。
- **互斥条款**：加入 MM 计划即退出 VIP 量费返佣和 affiliate 返佣。
- 评估：$1M 本金做 $100M+/月 = 日换手 >3.3x 本金，只有真 HFT 可行。**短期无现实路径。**

## 3. DEX 永续费率实况（2026）

### Lighter —— 零费率确认 ✅
来源：Lighter 官方文档 — Trading Fees
- **Standard 账户（默认）：maker 0 / taker 0，所有市场**。代价：人为延迟 taker 300ms / maker 200ms / cancel 200ms。
- Premium（可选）：maker 0.4bp / taker 2.8bp，质押 LIT 最多再减 30%，无附加延迟。
- 评估：**对延迟不敏感的薄 edge 策略（网格、慢因子）这是 0bp 直通**；对速度敏感做市，200ms 附加延迟是真实成本，需实测。无任何量/持仓门槛。

### Hyperliquid
- 基准：maker 1.5bp / taker 4.5bp。梯队按 14 天加权量：>$5M → 1.2bp；>$25M → 0.8bp；>$100M → 0.4bp；>$500M → 0。
- HYPE 质押折扣可叠加（>100 HYPE 减 10%…>500k 减 40%）+ 4% 推荐折扣。
- Maker rebate（-0.1bp 起）需平台 maker 份额 >0.5%——不现实。
- 评估：$5M/14d 极易 → maker ~1.0-1.08bp。深度最好的 perp DEX，但 0bp 要 $500M/14d。

### dYdX
- 30d 量梯队：<$1M → maker 1.0bp；≥$5M → 0.5bp；**≥$25M → maker 0**；≥$100M → **-0.7bp**；≥$200M → -1.1bp。
- 质押 DYDX 再打折；常有 Surge 赛季 100% 返费活动。
- 评估：**$25M/30d → maker 0bp 是 DEX 里第二现实的零费率路径**（日均 $833k 名义，杠杆下轻松）。

### edgeX / Paradex / GRVT / Extended
- **Paradex**：零售零费率（0/0），靠资金费差价变现。
- **Extended**：maker rebate 0.2–0.4bp，但需市场占比 ≥0.5%/1%——不现实。
- edgeX/GRVT：分层费率 + 推荐返佣 ~10%，无面向小户的公开 maker 返佣。

## 4. 其他 CEX 对比

| 交易所 | 低门槛亮点 | 关键费率 |
|---|---|---|
| **OKX** | VIP1 ≥$5M/30d → 1.6bp maker；**VIP3 ≥$50M → 1.0bp maker**（比 Binance VIP3 低 0.2bp） | 与 Binance 同构，VIP3 略优 |
| **Bybit** | **资产通道最友好：$100k → VIP1，$500k → VIP3（maker 1.4bp），零交易量要求** | ⚠️ **VIP4 起 API 订单占比 >20% 会被踢到 Pro 通道——对量化账户是硬伤**；Supreme VIP（$500M 衍生品量）才有 0 maker |
| **MEXC** | 合约 maker 0 / taker 2bp 对所有账户 | **CEX 里的 0 maker 无门槛选项**，但流动性深度远逊 Binance |

**对中小规模最友好排序**：Bybit 资产通道（量化账户受 API 条款限制，慎用）> Binance Holder 通道（无 API 限制）> OKX（纯量通道）。

## 5. Sub-broker / 返佣通道

- 机制真实：交易所 affiliate/broker 体系 → 第三方渠道把佣金的一部分（"最高 40%"）按周 USDT 返给被绑定交易者，**无最低量要求，不动 API/托管**。
- 叠加规则：对**正 maker 费**全额生效（Regular–VIP5）；maker 已为 0 或负时只返 taker；MM 计划参与者被排除。
- 合规评估：机制本身合规（官方体系衍生），但"最高 40%"是营销上限非保证；禁止自我推荐；需信任渠道方按约结算。**定位为叠加优化（VIP3 1.08bp → 有效 ~0.65bp），不是主路径。**

## 最终结论

1. **首选：Lighter Standard 账户，maker 0bp 立即生效、零门槛**。先在 Lighter 上验证薄 edge 策略在 200ms maker 延迟下的真实成交质量；若延迟吃掉 edge，转 Premium（0.4bp）。
2. **并行备选：dYdX 冲 $25M/30d → maker 0bp**；$5M 档（0.5bp）基本自动达成。
3. **CEX 主战场升级：Binance VIP3**。两条路任选：(a) Holder 通道 $1M 资产 + 100 BNB（零交易量、零合规风险）；(b) 期货 $50M/30d（杠杆下真实策略量即可）。得 maker 1.2bp / BNB 抵扣后 1.08bp，**已满足 ≤1.4bp 目标**；叠加 sub-broker 返佣有效 ~0.65bp。若迁 OKX，VIP3（$50M）maker 1.0bp 更优 0.08bp。
4. **明确不现实**：Binance MM Program（$100M+ 月量）、OKX ELP（VIP7/$1.5B+）、Hyperliquid/Extended 的份额制 maker rebate、Binance VIP9。
5. 附带选项：MEXC 合约 0 maker 无门槛，可作低流动性策略的测试场；Bybit 资产通道对量化账户因 API>20% 条款不推荐作为主路径。

---

*数据冲突裁决备注：Hyperliquid 默认档无 maker rebate（官档为准，第三方博客称默认有 -0.005% 系误读）；Binance VIP1/VIP2 现行费率（1.6/4.2bp、1.4/3.8bp）来自 2026-06 快照，反映 3 月新政后"门槛降、费率微升"的调整。*
