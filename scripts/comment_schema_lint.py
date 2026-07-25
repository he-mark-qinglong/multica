#!/usr/bin/env python3
"""comment_schema_lint — lint multica issue-comment first lines against the AGENTS.md comment schema.

Authoritative schema (AGENTS.md "Comment Schema Convention (mandatory 2026-07-19)"):

    [type=<TYPE>] <iso8601 timestamp+tz> <one-line summary>

where <TYPE> is one of STATUS | DECISION | EVIDENCE | KILL | ESCALATE | SIGNOFF | NUDGE | NOOP.
The tag must sit on the FIRST non-empty line (no leading blank lines, no body before the tag).
Seconds on the timestamp are optional. Timezone is REQUIRED and accepted in three forms:
    Z          (UTC zulu)
    +HH        (no minutes — canonical in our corpus, e.g. "+08")
    +HH:MM     (with optional colon; "+0800" and "+08:00" both OK)

The body (lines after the first) is free-form markdown and not validated here.

Public API (cross-card contract — T13 comment-janitor imports these symbols, do NOT change
the signature or return type without coordination):

    lint_comment(text: str) -> list[str]
        Returns a list of human-readable violation strings. Empty list == passes schema.
        Each violation is a single line; callers can join with "\n" for display.

    main(argv: list[str]) -> int
        CLI entrypoint. Reads files (or '-' / no-arg = stdin), prints one verdict per
        file on stdout, per-file failures on stderr. Exit codes:
            0  all inputs pass
            1  at least one input failed lint
            2  a file argument was not found (cannot stat)
"""
from __future__ import annotations

import re
import sys
from typing import Iterable

# 8 types are EXACT (uppercase). Lowercase, pluralization, or unknown tags -> fail.
# The case-sensitive match is enforced via the explicit alternation below AND by
# rejecting any [type=...] that doesn't hit the alternation.
TYPES = (
    "STATUS",
    "DECISION",
    "EVIDENCE",
    "KILL",
    "ESCALATE",
    "SIGNOFF",
    "NUDGE",
    "NOOP",
)

# Anchored first-line regex. The (Z|[+-]\d{2}(:?\d{2})?) group makes the timezone
# REQUIRED (timezone is mandatory per the schema), but flexible in punctuation.
#   Z          -> "Z"
#   +08        -> "+08"            (no minutes — canonical in our corpus)
#   +0800      -> "+0800"          (no colon)
#   +08:00     -> "+08:00"        (with colon)
#
# Seconds on the timestamp are optional: HH:MM  OR  HH:MM:SS.
# Summary must start with a non-whitespace character (\S) — empty/whitespace-only summaries fail.
_FIRST_LINE_RE = re.compile(
    r"^\[type=("
    + "|".join(TYPES)
    + r")\]\s+"
    + r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?"   # date + HH:MM[:SS]
    + r"(Z|[+-]\d{2}(:?\d{2})?)"                  # required timezone
    + r"\s+\S"                                    # non-empty summary
)


def _first_line(text: str) -> str:
    """Return the literal first line (splits on \\n, keeps raw whitespace).

    Leading blank lines intentionally do NOT exempt the schema — the AGENTS.md
    convention says "first line", meaning the actual first line of the body.
    """
    if "\n" in text:
        return text.split("\n", 1)[0]
    return text


def lint_comment(text: str) -> list[str]:
    """Return violations for `text` against the multica comment-schema convention.

    The returned list is empty iff the first line matches the schema. Each
    violation is a single human-readable line suitable for grep / cron sweep:

        "missing or unknown [type=...] tag"
        "missing ISO-8601 timestamp after tag"
        "invalid ISO-8601 timestamp ..."
        "timestamp missing timezone offset"
        "empty summary"
        "empty input"

    Cross-card contract (T13 comment-janitor): function name, signature, and
    `list[str]` return type are pinned. Do not change.
    """
    # Empty input is its own violation — distinct from "first line wrong".
    if not text:
        return ["empty input"]

    first = _first_line(text)

    # The tag prefix is required even when the regex itself fails for other
    # reasons — surface the most specific message the regex can give us by
    # dissecting the first line ourselves before falling back to the regex.
    tag_match = re.match(r"^\[type=([A-Za-z_]+)\]\s+", first)
    if not tag_match:
        # Either no [type=...] tag at all, or the tag has a stray character
        # (e.g. "[STATUS] ..." missing the "type=" prefix). Distinguish so the
        # message is grep-friendly.
        if first.startswith("[") and "type=" not in first.split("]", 1)[0]:
            return ["missing or unknown [type=...] tag (got [%s] without type= prefix)" % first.split("]", 1)[0].lstrip("[")]
        return ["missing or unknown [type=...] tag"]

    tag_value = tag_match.group(1)
    if tag_value not in TYPES:
        return ["missing or unknown [type=%s] tag (must be one of %s)" % (tag_value, "|".join(TYPES))]

    # Try the full regex. If it fails, pick a more specific message based on
    # which structural piece is missing.
    if _FIRST_LINE_RE.match(first):
        return []

    # The tag is correct, so the issue is timestamp/summary. Check timestamp shape.
    post_tag = first[tag_match.end():]
    # `post_tag` is " <timestamp> <summary>" — at minimum we'd expect a digit
    # immediately after the tag (with a separating space). If it's empty or
    # whitespace-only we have no timestamp at all.
    stripped = post_tag.lstrip()
    if not stripped:
        return ["missing ISO-8601 timestamp after tag"]

    # Tokenize the leading whitespace + timestamp.
    ts_match = re.match(
        r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?)"
        r"(Z|[+-]\d{2}:?\d{2}|[+-]\d{2})?"
        r"(\s+\S.*)?$",
        stripped,
    )
    if not ts_match:
        # First token after tag is not even a digit string.
        return ["invalid ISO-8601 timestamp: %r" % stripped.split()[0]]

    ts_text = ts_match.group(1)
    tz_text = ts_match.group(3)

    if tz_text is None:
        return ["timestamp missing timezone offset (got %r)" % ts_text]

    # Timestamp + timezone are fine — failure must be on the summary.
    summary_part = ts_match.group(4)
    if summary_part is None or not summary_part.strip():
        return ["empty summary after timestamp"]

    # We shouldn't reach here if the regex matched, but be defensive.
    return ["first line does not match schema: %r" % first]


def _iter_inputs(argv: list[str]) -> Iterable[tuple[str, str]]:
    """Yield (label, text) pairs from argv.

    Convention:
        - No args  -> stdin, labelled "<stdin>".
        - '-' as sole arg (or alongside other files) -> stdin, labelled "<stdin>".
        - Each positional arg -> one file path; label is the path itself.

    Missing-file detection is done by `main()`, not here, so that the caller
    can produce the correct exit code (2).
    """
    if not argv or argv == ["-"]:
        yield ("<stdin>", sys.stdin.read())
        return
    saw_stdin = False
    for arg in argv:
        if arg == "-":
            if not saw_stdin:
                yield ("<stdin>", sys.stdin.read())
                saw_stdin = True
        else:
            yield (arg, None)  # signal "read from disk later"


def main(argv: list[str]) -> int:
    """CLI entrypoint. See module docstring for exit-code semantics."""
    # Reproducible input shape: argv may be sys.argv[1:] or any list of strings.
    if argv is None:
        argv = sys.argv[1:]

    argv = list(argv)
    any_fail = False
    missing_file = False

    # First pass: stdin (if requested) — captured up-front so we can mix with files.
    stdin_label = None
    stdin_text = None
    if not argv or "-" in argv:
        stdin_text = sys.stdin.read()
        stdin_label = "<stdin>"
        if "-" in argv:
            argv.remove("-")
    if not argv and stdin_text is None:
        # No args and no stdin requested explicitly — fall through to "read stdin"
        stdin_text = sys.stdin.read()
        stdin_label = "<stdin>"

    if stdin_text is not None:
        violations = lint_comment(stdin_text)
        if violations:
            any_fail = True
            print("FAIL %s: %s" % (stdin_label, violations[0]), file=sys.stderr)
            for v in violations[1:]:
                print("    %s" % v, file=sys.stderr)
            print("FAIL %s" % stdin_label)
        else:
            print("OK %s" % stdin_label)

    # Second pass: file arguments.
    for path in argv:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except FileNotFoundError:
            print("MISSING %s" % path, file=sys.stderr)
            missing_file = True
            continue
        except OSError as exc:
            print("ERROR %s: %s" % (path, exc), file=sys.stderr)
            missing_file = True
            continue

        violations = lint_comment(text)
        if violations:
            any_fail = True
            print("FAIL %s: %s" % (path, violations[0]), file=sys.stderr)
            for v in violations[1:]:
                print("    %s" % v, file=sys.stderr)
            print("FAIL %s" % path)
        else:
            print("OK %s" % path)

    if missing_file:
        return 2
    if any_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
