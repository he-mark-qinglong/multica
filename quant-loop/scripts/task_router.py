#!/usr/bin/env python3
"""
task_router.py — rule-based task-difficulty router for the quant-loop.

Splits any task description into one of three tiers and maps each tier to a
provider/model pair that is already wired in ~/.kimi-code/config.toml:

    trivial -> minimax   (deterministic, cheap, fast; mechanical ops)
    medium  -> glm        (general coding / reasoning)
    hard    -> kimi-k3    (deep research, long-horizon thinking)

This is a pure rule-based classifier (no LLM call) intended to replace the
label-string hard-match that lived inline in agents_reclassify.py.  It is
deterministic, side-effect free, and independently testable.

CLI
---
    python task_router.py "refactor the backtest engine and compare 3 designs"
    -> {"difficulty": "hard", "provider": "kimi-k3", "model": "kimi-k3", ...}

    python task_router.py --difficulty "bump the version and commit"
    -> "trivial"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Dict

# ---------------------------------------------------------------------------
# Tier -> provider/model mapping (mirrors ~/.kimi-code/config.toml)
# ---------------------------------------------------------------------------
ROUTING: Dict[str, Dict[str, str]] = {
    "trivial": {"provider": "minimax", "model": "minimax-m3"},
    "medium": {"provider": "glm-smark", "model": "glm-5.2-smark"},
    # managed:kimi-tang already has k3 + k3-256k configured with a valid key
    # (api.kimi.com/coding/v1, health-checked 200). No separate Moonshot key needed.
    "hard": {"provider": "managed:kimi-tang", "model": "managed:kimi-tang/k3"},
}

# ---------------------------------------------------------------------------
# Rule keywords.  Order matters: HARD is checked first because a research task
# that happens to mention "list" is still HARD.  Word-boundary matching keeps
# "analysis" from matching inside "parenthesis".
# ---------------------------------------------------------------------------
HARD_KW = [
    # English — deep reasoning / research / architecture
    "research", "analyze", "analysis", "investigate", "diagnose",
    "architect", "architecture", "design",
    "refactor", "synthesis", "synthesize", "evaluate", "compar",
    "optimi", "derivation", "derive", "prove", "theorem",
    "forecast", "deep-dive", "deep dive",
    "comprehensive", "thesis", "essay", "critique",
    "roadmap", "whitepaper", "literature", "hypothes",
    "regime", "cointegration", "factor model",
    # Chinese
    "调研", "研究", "分析", "架构", "设计", "评估", "优化",
    "论证", "综述", "深度", "推演", "建模", "预测", "审查",
    "规划", "路线图", "假设", "协整", "因子模型",
]

TRIVIAL_KW = [
    # English — deterministic / mechanical
    "format", "lint", "rename", "typo", "bump", "version", "list",
    "count", "grep", "find ", "status", "health", "ping", "restart",
    "deploy", "start", "stop", "sync", "fetch", "download", "upload",
    "copy", "move", "mkdir", "chmod", "commit", "push", "log", "tail",
    "dump", "serialize", "parse", "delete", "cleanup", "clear", "reset",
    "refresh", "cron", "schedule", "export", "import", "echo", "print",
    "rename-file", "fix-import", "whitespace", "indent",
    # Routine autopilot operations (not "hard" despite keywords)
    "validate", "scan", "archive", "janitor", "orphan", "heartbeat",
    "patrol", "watchdog", "stale", "ledger", "auto-archive",
    # Chinese
    "格式化", "重命名", "列表", "统计", "查询状态", "重启", "部署",
    "同步", "清理", "日志", "导出", "导入", "提交", "备份",
    "巡检", "心跳", "归档", "看门狗",
]

# Length thresholds — a very long, multi-step brief leans HARD even without an
# explicit keyword hit; a one-liner leans TRIVIAL.
_LONG_THRESHOLD = 320   # chars
_SHORT_THRESHOLD = 24   # chars


def _word_match(text: str, keywords: list[str]) -> list[str]:
    """Return the subset of keywords present in *text* (case-insensitive)."""
    low = text.lower()
    hits = []
    for kw in keywords:
        if re.search(r"(?<![a-z])" + re.escape(kw.lower()) + r"", low):
            hits.append(kw)
    return hits


def classify_difficulty(task: str) -> str:
    """Classify a task description into ``trivial`` / ``medium`` / ``hard``.

    Rules (evaluated in priority order):
      1. Any HARD keyword present  -> hard
      2. Any TRIVIAL keyword present -> trivial
      3. Length heuristic: very long brief -> hard, very short -> trivial
      4. default -> medium
    """
    if not task or not task.strip():
        return "medium"
    text = task.strip()

    hard_hits = _word_match(text, HARD_KW)
    if hard_hits:
        return "hard"

    trivial_hits = _word_match(text, TRIVIAL_KW)
    if trivial_hits:
        return "trivial"

    n = len(text)
    if n >= _LONG_THRESHOLD:
        return "hard"
    if n <= _SHORT_THRESHOLD:
        return "trivial"
    return "medium"


def route_task(task: str) -> dict:
    """Return the full routing decision for *task* as a dict.

    Keys: difficulty, provider, model, matched, rationale, confidence.
    """
    text = (task or "").strip()
    difficulty = classify_difficulty(text)
    tier = ROUTING[difficulty]

    # rebuild matched keywords for the rationale
    hard_hits = _word_match(text, HARD_KW)
    trivial_hits = _word_match(text, TRIVIAL_KW)
    if difficulty == "hard":
        matched = hard_hits
        confidence = "high" if hard_hits else "medium"  # medium if length-only
    elif difficulty == "trivial":
        matched = trivial_hits
        confidence = "high" if trivial_hits else "medium"
    else:
        matched = []
        confidence = "medium"

    if difficulty == "hard":
        rationale = "matched hard keyword(s): " + ", ".join(matched) if matched \
            else f"long multi-step brief ({len(text)} chars) without trivial signal"
    elif difficulty == "trivial":
        rationale = "matched trivial keyword(s): " + ", ".join(matched) if matched \
            else f"very short task ({len(text)} chars)"
    else:
        rationale = "no strong tier signal; default general-coding tier"

    return {
        "difficulty": difficulty,
        "provider": tier["provider"],
        "model": tier["model"],
        "matched": matched,
        "rationale": rationale,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli() -> int:
    ap = argparse.ArgumentParser(
        description="Rule-based task difficulty router for the quant-loop.")
    ap.add_argument("task", nargs="*", help="task description (quoted)")
    ap.add_argument("--difficulty", action="store_true",
                    help="print only the difficulty tier (trivial/medium/hard)")
    args = ap.parse_args()

    task = " ".join(args.task) if args.task else sys.stdin.read()
    if args.difficulty:
        print(classify_difficulty(task))
        return 0

    decision = route_task(task)
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
