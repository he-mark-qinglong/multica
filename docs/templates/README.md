# docs/templates — long-form doc templates

Reusable skeletons for incident retrospectives and other long-form docs
that the multica workspace writes after the fact. Kept under
`docs/templates/` (not `quant-loop/_shared/templates`, which is for runtime
strategy scaffolding) so they are reachable by every agent — ops, research,
code, and curator — without a project hop.

## Files

| File | Purpose | Usage cadence |
|---|---|---|
| `postmortem.md` | Standard incident retrospective template (full + P2 trim). Sections §1–§14. | every P0/P1 incident; P2 may use §13 trim |
| `postmortem-example-disk-saturation-2026-07-10.md` | Worked example reconstructed from `quant-loop/docs/decisions/deploy-incidents.md` §Bucket 1. Doubles as a verification artifact and as a worked how-to-fill-out-the-template reference. | reference only — not a real incident doc, the real one lives in `quant-loop/docs/decisions/` |
| `README.md` | This file. | nav |

## Out of scope (kept elsewhere)

- **Runbooks** (real-time signal → action): `docs/runbooks/`
- **Per-project retrospectives** (family seals, archive decisions, deploy
  incidents): `quant-loop/docs/decisions/*.md` and
  `docs/runbooks/scripts/`
- **Strategy / variant scaffolding**: `quant-loop/_shared/templates/`
- **Helm chart templates**: `deploy/helm/multica/templates/`

## How to fill in `postmortem.md`

1. Copy the frontmatter block (§1) to the top of a new file. Pick an
   unused `PM-YYYY-NNN` id from the parent project's
   `metadata.decision` register (if the register does not yet exist,
   file that as a §9 #1 of *this* doc's chain — see worked example §9 #4).
2. Fill every `<…>` placeholder, including in §4.3 (data loss / integrity)
   which is mandatory even when the answer is "none".
3. **No `<…>` may remain** when the doc is merged — if you cannot fill a
   placeholder, write `unknown — investigated, no signal` instead.
4. Compute MTTD / MTTR-contain / MTTR-full from §3 and record against the
   §3.1 targets. Missing a target forces a §9 action item.
5. Every §8.2 ("What didn't") item **must** map to a §9 row. Skipping this
   turns the postmortem into a complaint.
6. §9 rows must have a Verification column that answers *"what evidence
   proves this action is done and effective?"*. Vague verification =
   rejection by §14 anti-patterns.
7. §11 sign-off is mandatory: author + L4 / smark. **Do not merge without
   both signatures.**
8. P2 trim: use §13. Anything that recurs three times as P2 must be
   rewritten in full.

## Versioning

Templates in this directory are versioned with the workspace. When the
postmortem template changes (e.g. a new severity tier is added), bump the
file header revision line and add a one-paragraph note here. Examples
should be regenerated against the new template — do not let the worked
example lag the canonical template.