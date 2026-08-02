# ruff lint 基线 (J16)

日期: 2026-08-02 ｜ ruff 0.15.22 ｜ 配置: 根 `pyproject.toml` [tool.ruff]

行宽 100, target py312, 规则集 E/F/W/I/UP；D 系列（docstring 风格）未启用——docstring 约定鼓励但不强制。

**本基线只记录存量，不做修复。** 新代码/改动代码应符合该标准；存量违规按目录逐步消化。

## 复现命令

```bash
pip3 install ruff
ruff check _shared/ --statistics
```

## 基线输出 (`ruff check _shared/ --statistics`)

```
417	UP006	[*] non-pep585-annotation
190	UP035	[-] deprecated-import
168	UP045	[*] non-pep604-annotation-optional
115	I001 	[*] unsorted-imports
105	F401 	[*] unused-import
 41	E501 	[ ] line-too-long
 24	E741 	[ ] ambiguous-variable-name
 22	W292 	[*] missing-newline-at-end-of-file
 11	UP042	[ ] replace-str-enum
  9	F841 	[-] unused-variable
  9	UP037	[*] quoted-annotation
  8	E402 	[ ] module-import-not-at-top-of-file
  7	W293 	[-] blank-line-with-whitespace
  6	E701 	[ ] multiple-statements-on-one-line-colon
  5	UP017	[*] datetime-timezone-utc
  4	UP007	[*] non-pep604-annotation-union
  3	E731 	[ ] lambda-assignment
  2	F541 	[*] f-string-missing-placeholders
  1	F811 	[*] redefined-while-unused
  1	UP015	[*] redundant-open-modes
Found 1148 errors.
[*] 917 fixable with the `--fix` option (26 hidden fixes can be enabled with the `--unsafe-fixes` option).
```
