# 代码审查清单 (J19)

适用于 quant-loop 仓库所有 PR，尤其是 `_shared/` 基础设施与
`strategies/` 新策略。审查人逐项确认；任一项不满足即 request changes。

## 1. 防前视 (look-ahead)

- [ ] 信号计算只使用 ≤ 当前 bar 的数据（无 `shift(-k)`、无未来窗口统计量）。
- [ ] forward return / 标签只在评估与验证代码中出现，不进入特征。
- [ ] 回测撮合使用下一根 bar 开盘价或显式延迟模型，不用当期收盘价成交。
- [ ] 参数选择 / 阈值调优基于 train 段，OOS 段只评估（参考
      `_shared/validation/cpcv.py` 的 purge/embargo 约定）。

## 2. 不变量测试

- [ ] 新模块带 pytest，测试文件与模块同目录、同名 `test_*.py`，开头
      `sys.path.insert(0, "/Users/mark/multica/quant-loop")`。
- [ ] 关键不变量有显式断言：权重和 = 1、资金守恒（转入 = 转出）、
      权益非负、指标边界（|IC| ≤ 1、回撤 ≤ 100%）等。
- [ ] 边界用例：空输入、单样本、零基线、常数序列不得崩溃或产出 NaN 静默。
- [ ] 核心逻辑为纯函数，冻结 dataclass 承载结果（仓库约定）。

## 3. 成本模型

- [ ] 回测计入手续费 + 滑点，且口径与 `_shared/validation/fee_shock.py`
      一致：每笔成本 = 全名义本金 × bps / 1e4（禁止历史遗留的 0.005 缩放，
      SMA-36566）。
- [ ] 提供 fee shock 结果（±若干 bps 档）证明策略对成本不敏感，
      或在 PR 说明中给出成本假设依据。
- [ ] 部分成交 / 延迟若相关，使用 `_shared/partial_fill.py` /
      `_shared/latency_model.py`，不另起炉灶。

## 4. 新策略 gate

- [ ] 新策略 PR 必须附 gate 结果：`strategies/<name>/metrics.json` 通过
      `_shared/gates/enforce.py`（CI 工作流 `strategy-gate` 自动校验，
      PR 描述中粘贴输出）。
- [ ] 附参数敏感性结论（`_shared/validation/sensitivity.py`）：
      存在参数悬崖（|弹性| > 2）时必须在 PR 中说明并论证。
- [ ] 附 CPCV / OOS 摘要，不允许只贴单一全样本回测曲线。

## 5. 通用质量

- [ ] `ruff check` 不引入新违规（基线见 `docs/lint-baseline.md`）。
- [ ] 新公开函数有 docstring；引用外部方法时给出文献出处（仓库约定）。
- [ ] 不修改与 PR 目标无关的文件；仓库级文件（pyproject.toml、
      docs/、.github/）变更在 PR 描述中单独说明。
- [ ] API 文档同步：新增/变更 `_shared/` 公开接口后运行
      `python3 scripts/gen_api_docs.py`。
