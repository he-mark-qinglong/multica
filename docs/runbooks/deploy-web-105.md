# Runbook — Deploy web (Next.js) to smark@192.168.0.105:3000

> 适用范围：`apps/web`（`@multica/web`，Next.js）部署到 `smark@192.168.0.105` 的 `:3000`。
> 执行机：mac（需有 repo worktree + ssh 免密到 .105，两者已实测可用）。
> 文档描述的是「部署 → 冒烟 → 回滚」完整链路，**仅写文档，本卡不执行部署**。
> 文档所有命令均可照抄；执行前请先读完 §Preflight 任一不过即停。

---

## 1. 用途与范围

将本地 worktree 中的 `apps/web`（及其依赖的 `packages/` 等共享代码）通过 `scripts/deploy.sh web` 部署到：

- 目标主机：`smark@192.168.0.105`（DEPLOY_HOST，可在脚本内 `export DEPLOY_HOST=...` 覆盖）
- 远端目录：`/home/smark/multica`（REMOTE_DIR，同上）
- 远端服务：`pnpm --filter @multica/web build` 产物 → `nohup pnpm start`（即 `next start`）监听 `:3000`
- 访问入口：`http://192.168.0.105:3000`，workspace 路由例：`http://192.168.0.105:3000/smark/compare`

预期前置：W2 的 T6-T9 前端代码已合并（`gate_status` 三态支持 `no-data`、KILL 灰显 + kill_reason 悬停、一句话 verdict 区块均由其他片交付）。T11（server 部署 + strict gate 回填）应先于本卡执行，以便 compare 页面有真实 fail/no-data 数据可看。

---

## 2. Preflight

任一项不过则停止本次部署，不要带病上线。

```bash
cd /Users/mark/multica

# 2.1 本地类型 + 单测全绿
pnpm typecheck && pnpm test
```

```bash
# 2.2 worktree 脏文件自检（web 分支 rsync 同步整个 worktree，无 --delete，见 deploy.sh:30-35）
git status --short apps/web packages/
```

> **警告**：`scripts/deploy.sh` 的 web 分支用 `rsync -au` 同步**整个 worktree**
> （`scripts/deploy.sh:32-35`，无 `--delete`、`--update` 让 cron 在远端写入的文件优先），
> 因此本机 worktree 里**任何未提交改动**（哪怕不是 `apps/web/` 自己的）都会被一起推到线上。
> 如有他人未提交改动，先从干净 worktree 部署，或与并行 workstream 协调。

```bash
# 2.3 ssh 免密可达
ssh -o BatchMode=yes -o ConnectTimeout=5 smark@192.168.0.105 true && echo SSH_OK
```

```bash
# 2.4 远端 :3000 部署前基线（确认本就活着，部署中断后我们也知道是回退到的状态）
curl -s -o /dev/null -w 'pre-deploy http=%{http_code}\n' http://192.168.0.105:3000
```

期望：`http=200`（2026-07-25 实测 `200`）。若非 200，先排查 .105 上 web 是否本就在跑、
或上一次部署是否残留（看 `web-prod.log`）。

```bash
# 2.5 部署脚本语法自检（防止本地 copy/edit 引入语法错误）
bash -n scripts/deploy.sh && echo SYNTAX_OK
```

---

## 3. 部署

```bash
cd /Users/mark/multica
scripts/deploy.sh web
```

脚本内联执行步骤（按 `scripts/deploy.sh:27-61`）：

1. **源码 rsync**：`rsync -auz` 把 worktree 推到 `smark@192.168.0.105:/home/smark/multica/`，
   排除 `.git / node_modules / data / dist / test-results / .next / .turbo / .env /
   server/bin / .deploy-* / *.log / .DS_Store`（`deploy.sh:32-34`）。注意：
   无 `--delete`，所以远端独有的目录/文件不会被清掉（如 cron 落地的产物）；
   无 `--update` 的覆盖语义对远端更新过的文件优先保留。
2. **远端 `pnpm install --frozen-lockfile`**：锁文件严格一致，禁止 lockfile 自动更新上线。
3. **远端 `pnpm --filter @multica/web build`**：跑 `next build --webpack`（含 fumadocs-mdx 预处理），
   产物在 `apps/web/.next/`，数分钟。
4. **重启 `next start`**：
   - `pkill -f 'next start' 2>/dev/null || true`
   - `pkill -f 'next-server' 2>/dev/null || true`
   - `for i in 1..10`: `ss -tln | grep -q ':3000 ' || break`（最多 10s 等待旧进程释放端口）
   - `cd apps/web && nohup pnpm start >> "$HOME/multica-tunnel/web-prod.log" 2>&1 & disown`
5. **轮询健康检查**：每 2s 一次 `curl -sf http://localhost:3000`，最多 60 轮（≈120s）；
   任一次成功 → 打印 `web OK on :3000` 并退出 0；
   超时仍 200 → 打印 `ERROR: web did not come up on :3000 — check
   $HOME/multica-tunnel/web-prod.log` 并 `exit 1`（**注意：web 分支没有自动回滚**，见 §5）。

部署窗口注意：本卡与 T11（server 部署）共用一个 .105 部署窗口，避免对 daemon /
server binary 反复重启；与他人 workstream 的部署协调时序。

---

## 4. 部署后验证（冒烟）

按顺序执行，任一不过即按 §5 回滚。

### 4.1 HTTP 探针

```bash
curl -s -o /dev/null -w 'http=%{http_code}\n' http://192.168.0.105:3000
```

期望：`http=200`（2026-07-25 实测 `200`）。

### 4.2 compare 页面浏览器清单

浏览器打开 `http://192.168.0.105:3000/smark/compare`（slug `smark` 已从线上 DB 核实；
若 workspace slug 变更，先用 `multica workspace list` 查实际 slug），
对照以下清单打勾：

1. **gate 徽章出现三态**：`pass` / `fail` / `no-data` 三种颜色或图标区分；
   `no-data` 渲染为**灰色**（与 `pass`/`fail` 视觉上明确不同，避免误读为「未知」。
   缺数据 ≠ 不通过）。
2. **KILL 行灰显**：被 KILL 的策略行整行降透明度 / 灰阶，鼠标悬停显示 `kill_reason`；
   若该 KILL 行没有 `kill_reason`（存量数据），悬停回退显示 `divergence_flag` 值。
3. **一句话 verdict 区块**：detail 面板顶部对 `extra.verdict` 非空的行渲染一句话 verdict
   （如 `CV_PASS` / `PASS` / `HOLD` / `KILL` / `UNTESTED`）；
   `extra.verdict` 缺失的行**不渲染**该区块（不显示空白占位）。
4. **过滤后页面不为空**：strict gate 上线后，原 `pass` 但缺 `profit_factor` 等必填的行
   现在应 `fail` / `no-data`，且**置灰展示而非从列表消失**（行仍可见，便于排查）。

异常时看日志：

```bash
ssh smark@192.168.0.105 'tail -100 ~/multica-tunnel/web-prod.log'
```

### 4.3 （可选）从本机 CLI 抽查 server 端 gate 翻转

T11 部署 + 回填之后，线上 strict gate 应已生效。可用以下命令从本机指向 .105 抽查：

```bash
export MULTICA_SERVER_URL=http://192.168.0.105:8080
export MULTICA_WORKSPACE_ID=<smark workspace UUID>   # 未配置时先用 multica config 查
multica metrics query --campaign mtf-xs-pairs --output json | \
  /Users/mark/sdk/mamba-envs/trading/bin/python3 -c "
import json,sys
rows=[m for m in json.load(sys.stdin)['metrics'] if m['iteration']=='mtf_xs_pairs_1m_15m_2h_h3_20260718']
assert rows, 'no rows for that iteration'
print('gate_status =', rows[0]['gate_status'])"
```

期望：`gate_status = fail`（该行 `profit_factor` 为 NULL，旧语义 `pass`、strict 语义 `fail`），
或在 4.2 浏览器中能看到这一行置灰 + 徽章 `fail`。

---

## 5. 回滚

> **web 分支没有 binary 备份**（不像 server 部署有 `server.bak-<stamp>` 自动回滚），
> 所有回滚都是「重打」语义：把代码恢复到上一个好的状态 → 重跑 §3。

### 5.1 部署脚本失败（§3 exit 1）

脚本内部已轮询 120s 仍未 200 即退出 1，但**没有**像 server 分支那样自动把 binary 换回旧版。
需要人工处理：

```bash
# 看日志第一手定位：是 build 错、还是 next start 起不来、还是路由没就绪
ssh smark@192.168.0.105 'tail -200 ~/multica-tunnel/web-prod.log'
```

按以下两种思路选一：

- **若仅构建产物问题**（依赖错、TypeScript 错等）：ssh 到 .105 重建即可，无需回退代码。

  ```bash
  ssh smark@192.168.0.105 'cd /home/smark/multica && pnpm --filter @multica/web build'
  ssh smark@192.168.0.105 'pkill -f "next start"; pkill -f "next-server"; sleep 2'
  ssh smark@192.168.0.105 'cd /home/smark/multica/apps/web && \
    nohup pnpm start >> $HOME/multica-tunnel/web-prod.log 2>&1 & disown'
  ssh smark@192.168.0.105 'curl -sf http://localhost:3000 && echo OK || echo FAIL'
  ```

- **若代码本身就是问题**（本次部署引入了回归）：在本机 worktree `git revert` 或
  `git reset`（按团队约定，**注意：在 worktree 里只 revert 自己的提交，不要覆盖他人未提交
  改动**）→ 重跑 §3。

### 5.2 部署后线上冒烟失败（§4.3 不通过、§4.2 浏览器清单不符）

1. 在本机 worktree 排查是否为本片提交引入；若是，`git revert` 后重跑 §3。
2. 若非本片原因，回退到上一个稳定的部署对应 commit/branch，重跑 §3。
3. 回滚后**重跑 §4 全套冒烟**确认 `http=200` 且浏览器清单恢复。

### 5.3 回滚验证（必跑）

```bash
curl -s -o /dev/null -w 'http=%{http_code}\n' http://192.168.0.105:3000
```

期望：`http=200`。

```bash
ssh smark@192.168.0.105 'tail -50 ~/multica-tunnel/web-prod.log | grep -E "Ready|started|error" || true'
```

期望：能看到 `Ready` / `started` 类关键字，无 `error` / `failed` 关键字。

---

## 6. 故障线索索引

| 现象 | 看哪里 |
|---|---|
| `:3000` 起来又挂 | `ssh smark@192.168.0.105 'tail -100 ~/multica-tunnel/web-prod.log'` |
| `:3000` 端口没释放，部署超时 | `ssh smark@192.168.0.105 'ss -tlnp \| grep :3000'` |
| 客户端白屏 / 500 | 浏览器 devtools + `web-prod.log`；同时 `curl localhost:8080/healthz` 确认 server 还活着 |
| compare 页面 `no-data` 徽章没出现 | 服务端 strict gate 可能还没回填（看 T11 部署结果），或 workspace slug 写错 |
| `pnpm install` 锁文件冲突 | 锁文件没冻结（`frozen-lockfile` 报错）→ 本机 `pnpm install` 后重 commit `pnpm-lock.yaml` 再部署 |
| `next build` OOM | 远端机器内存不足；先 `free -h` 看，必要时减少并发（`NEXT_BUILD_WORKERS=1`） |

---

## 附录 A：相关代码锚点（写文档时引用，部署时不必读）

- `scripts/deploy.sh:27-61`：web 分支全文（rsync → pnpm install --frozen-lockfile → build → restart → 轮询）。
- `apps/web/app/[workspaceSlug]/(dashboard)/compare/page.tsx`：12 行薄壳，仅渲染 `<ComparePage />`。
- `apps/web/package.json`：`build = fumadocs-mdx && next build --webpack`；`start = next start`。
- `packages/views/compare/utils/verdict.ts`（其他片交付）：`readVerdict` 决定 KILL 判定 + 一句话 verdict 来源（`extra.verdict` / `extra.kill_reason`）。
- `quant-loop/docs/metrics-blob-convention.md`（w2-s5-T10a 交付）：发布侧往 blob 写 `verdict` / `kill_reason` / `kill_evidence` 三个键的硬契约。

## 附录 B：环境变量与配置

- `DEPLOY_HOST`：默认 `smark@192.168.0.105`，可临时 `export DEPLOY_HOST=...` 覆盖。
- `REMOTE_DIR`：默认 `/home/smark/multica`，同上。
- `MULTICA_SERVER_URL`：CLI 直连 .105 server 时设为 `http://192.168.0.105:8080`。
- `MULTICA_WORKSPACE_ID`：smark workspace 的 UUID（先用 `multica workspace list` 查）。
- `MULTICA_TOKEN`：CLI 鉴权 token，缺失时 `multica config set token ...`。