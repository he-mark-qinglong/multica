<!--
审查清单全文见 docs/code-review.md。新策略 PR 必须填写第 2 节并附 gate 结果。
-->

## 变更说明

<!-- 做了什么、为什么。仓库级文件（pyproject.toml / docs/ / .github/）变更请单独说明。 -->

## 新策略 gate 结果（新策略必填）

<!-- 粘贴 `python _shared/gates/enforce.py strategies/<name>/metrics.json` 的输出，
     以及参数敏感性 / CPCV 摘要。CI 工作流 strategy-gate 会复核。 -->

```
<gate output>
```

## 审查清单

- [ ] 无前视：信号只用 ≤ 当前 bar 的数据；撮合不含当期收盘价成交
- [ ] 成本口径与 `_shared/validation/fee_shock.py` 一致（全名义本金 × bps/1e4）
- [ ] 新增/修改逻辑带 pytest，含不变量断言（权重和=1、资金守恒、非负等）
- [ ] 核心逻辑为纯函数，结果用 frozen dataclass
- [ ] `ruff check` 未引入新违规（基线：docs/lint-baseline.md）
- [ ] 新策略：gate 结果已附（上方），参数悬崖已说明
- [ ] `_shared/` 公开接口变更后已运行 `python3 scripts/gen_api_docs.py`

## 测试

<!-- 跑了哪些测试命令、结果如何 -->
