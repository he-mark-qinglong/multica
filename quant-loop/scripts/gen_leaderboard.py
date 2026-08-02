#!/usr/bin/env python3
"""Generate the 200-metric infrastructure leaderboard HTML."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from leaderboard_metrics import CATEGORIES, METRICS, MULTICA

OUT = Path("/Users/mark/Desktop/strategies-leaderboard/infra-leaderboard-200.html")


def score_cell(v, evidence=""):
    cls = {2: "s2", 1: "s1", 0: "s0"}[v]
    label = {2: "●", 1: "◐", 0: "○"}[v]
    title = f' title="{evidence}"' if evidence else ""
    return f'<td class="sc {cls}"{title}>{label}</td>'


def cat_score(scores, cat):
    ids = [m[0] for m in METRICS if m[1] == cat]
    got = sum(scores.get(i, 0) for i in ids)
    full = len(ids) * 2
    return got, full


def render(engines: dict[str, dict[str, int]], evidence: dict[str, dict[str, str]], notes: dict[str, str],
           changelog: str = ""):
    cats = list(CATEGORIES.keys())
    # per-category totals
    totals = {e: {c: cat_score(s, c) for c in cats} for e, s in engines.items()}
    grand = {e: (sum(v[0] for v in totals[e].values()), sum(v[1] for v in totals[e].values()))
             for e in engines}
    ranked = sorted(engines.keys(), key=lambda e: -grand[e][0])

    rows = []
    for mid, cat, name, desc in METRICS:
        cells = "".join(
            score_cell(engines[e].get(mid, 0), evidence.get(e, {}).get(mid, ""))
            for e in ranked
        )
        rows.append(
            f'<tr data-cat="{cat}"><td class="mid">{mid}</td>'
            f'<td class="mname">{name}<div class="mdesc">{desc}</div></td>{cells}</tr>'
        )

    cat_headers = "".join(f'<th>{c}<br><span class="catfull">{CATEGORIES[c].split()[0]}</span></th>' for c in cats)
    cat_rows = ""
    for e in ranked:
        tds = "".join(
            f'<td class="catsc">{totals[e][c][0]}<span class="dim">/{totals[e][c][1]}</span></td>'
            for c in cats
        )
        g = grand[e]
        pct = g[0] / g[1] * 100
        medal = {0: "🥇", 1: "🥈", 2: "🥉"}.get(ranked.index(e), "")
        hl = ' class="multica-row"' if e == "multica" else ""
        cat_rows += (f'<tr{hl}><td class="ename">{medal} {e}'
                     f'<div class="enote">{notes.get(e, "")}</div></td>{tds}'
                     f'<td class="grand">{g[0]}<span class="dim">/{g[1]}</span>'
                     f'<div class="pct">{pct:.0f}%</div></td></tr>')

    engine_headers = "".join(f'<th class="eh">{e}</th>' for e in ranked)
    filter_btns = "".join(
        f'<button class="fbtn" data-cat="{c}">{c} {CATEGORIES[c].split()[0]}</button>'
        for c in cats
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>交易基础设施 200 指标评分榜</title>
<style>
:root {{ --bg:#0a0e14; --surface:#111720; --surface2:#1a2030; --border:#252d3d;
  --accent:#00d4aa; --accent2:#6c5ce7; --warn:#fdcb6e; --danger:#e17055;
  --text:#c8d3e0; --text-dim:#7a8699; --bright:#fff; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,'SF Pro Display','Segoe UI',system-ui,sans-serif;
  background:var(--bg); color:var(--text); }}
.container {{ max-width:1500px; margin:0 auto; padding:24px 20px 60px; }}
h1 {{ font-size:1.8rem; color:var(--bright); margin-bottom:6px; }}
h1 .hl {{ color:var(--accent); }}
.sub {{ color:var(--text-dim); font-size:0.9rem; margin-bottom:24px; }}
.legend {{ display:flex; gap:16px; margin:14px 0 20px; font-size:0.82rem; color:var(--text-dim); }}
.legend span b {{ margin-right:4px; }}
.s2 {{ color:var(--accent); }} .s1 {{ color:var(--warn); }} .s0 {{ color:#3a4356; }}
.filters {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:18px; }}
.fbtn {{ background:var(--surface2); border:1px solid var(--border); color:var(--text);
  padding:5px 12px; border-radius:100px; font-size:0.78rem; cursor:pointer; }}
.fbtn:hover, .fbtn.active {{ border-color:var(--accent); color:var(--accent); }}
table {{ border-collapse:collapse; width:100%; }}
.wrap {{ background:var(--surface); border:1px solid var(--border); border-radius:14px;
  overflow:auto; max-height:78vh; }}
th, td {{ padding:8px 10px; border-bottom:1px solid rgba(37,45,61,.5); font-size:0.82rem;
  text-align:center; white-space:nowrap; }}
thead th {{ position:sticky; top:0; background:var(--surface2); z-index:3;
  font-size:0.75rem; color:var(--text-dim); }}
thead th.eh {{ color:var(--bright); font-weight:700; font-size:0.82rem; }}
td.mid {{ color:var(--text-dim); font-family:'SF Mono',monospace; font-size:0.72rem; }}
td.mname {{ text-align:left; color:var(--bright); font-weight:600; position:sticky;
  left:0; background:var(--surface); z-index:1; min-width:200px; }}
.mdesc {{ font-weight:400; color:var(--text-dim); font-size:0.72rem; }}
td.sc {{ font-size:1rem; }}
tr[data-cat] td:first-child {{ border-left:3px solid transparent; }}
tr[data-cat="A"] td:first-child {{ border-left-color:#6c5ce7; }}
tr[data-cat="C"] td:first-child {{ border-left-color:#00d4aa; }}
tr[data-cat="G"] td:first-child {{ border-left-color:#fdcb6e; }}
.catsc {{ font-weight:700; color:var(--bright); }}
.dim {{ color:var(--text-dim); font-weight:400; font-size:0.72rem; }}
.grand {{ font-weight:800; color:var(--accent); font-size:0.95rem; }}
.pct {{ font-size:0.75rem; color:var(--accent); }}
.ename {{ text-align:left; font-weight:700; color:var(--bright); position:sticky;
  left:0; background:var(--surface2); z-index:1; }}
.enote {{ font-weight:400; font-size:0.7rem; color:var(--text-dim); max-width:260px;
  white-space:normal; }}
.multica-row td {{ background:rgba(0,212,170,.06); }}
.multica-row .ename {{ color:var(--accent); }}
h2 {{ font-size:1.2rem; color:var(--bright); margin:32px 0 12px; }}
</style></head><body><div class="container">
<h1>交易基础设施 <span class="hl">200 指标</span>评分榜</h1>
<div class="sub">multica vs 主流开源交易引擎 · 10 大类 × 20 指标 · ●=完整 ◐=部分 ○=缺失 · 2026-08-02 v5</div>
{changelog}
<div class="legend">
  <span><b class="s2">●</b>完整实现 (2分)</span>
  <span><b class="s1">◐</b>部分实现 (1分)</span>
  <span><b class="s0">○</b>缺失 (0分)</span>
  <span style="margin-left:auto">悬停单元格查看证据</span>
</div>

<h2>总榜（按大类汇总）</h2>
<div class="wrap"><table>
<thead><tr><th style="text-align:left">引擎</th>{cat_headers}<th>总分</th></tr></thead>
<tbody>{cat_rows}</tbody></table></div>

<h2>200 指标明细</h2>
<div class="filters"><button class="fbtn active" data-cat="all">全部</button>{filter_btns}</div>
<div class="wrap"><table id="detail">
<thead><tr><th>#</th><th style="text-align:left">指标</th>{engine_headers}</tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
</div>
<script>
document.querySelectorAll('.fbtn').forEach(b => b.addEventListener('click', () => {{
  document.querySelectorAll('.fbtn').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  const c = b.dataset.cat;
  document.querySelectorAll('#detail tbody tr').forEach(r => {{
    r.style.display = (c === 'all' || r.dataset.cat === c) ? '' : 'none';
  }});
}}));
</script>
</body></html>"""
    OUT.write_text(html)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    # placeholder engines — replaced by scored data after research
    render({"multica": MULTICA}, {}, {"multica": "本项目"})
