#!/usr/bin/env bash
# archive_trading_repo.sh — 1-click archive for he-mark-qinglong/trading
#
# Strategy:
#   1. Verify gh CLI is installed and authenticated as he-mark-qinglong.
#   2. Attempt archive via `gh repo archive` (GraphQL mutation via REST).
#   3. If archiveRepository scope is missing on the PAT, the call returns 403
#      with "Resource not accessible by personal access token". Exit non-zero
#      with a precise message pointing the operator at the web-UI fallback
#      (see archive_trading_repo.md) and the exact PAT scope to add.
#
# IMPORTANT: this script does NOT auto-add scopes. Token scope changes require
# a browser trip to https://github.com/settings/tokens — there is no CLI to
# patch a PAT's scope from inside a CI/dev box.
#
# Run:    bash archive_trading_repo.sh
# Owner:  smark

set -euo pipefail

REPO_OWNER="he-mark-qinglong"
REPO_NAME="trading"
EXPECTED_USER="he-mark-qinglong"

# -------- color helpers (only if stdout is a tty) --------
if [ -t 1 ]; then
    RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; CYN=$'\033[0;36m'; RST=$'\033[0m'
else
    RED=''; GRN=''; YLW=''; CYN=''; RST=''
fi

say()  { printf '%s\n' "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$YLW" "$RST" "$*"; }
fail() { printf '%s[FAIL]%s %s\n' "$RED" "$RST" "$*" >&2; }
ok()   { printf '%s[ OK]%s %s\n' "$GRN" "$RST" "$*"; }

# -------- step 1: gh installed? --------
say "${CYN}==> Step 1/4: checking gh CLI${RST}"
if ! command -v gh >/dev/null 2>&1; then
    fail "gh CLI not found on PATH. Install:  brew install gh   (or visit https://cli.github.com/)"
    exit 2
fi
GH_VERSION="$(gh --version | head -1)"
ok "gh present: $GH_VERSION"

# -------- step 2: gh authenticated? as whom? --------
say "${CYN}==> Step 2/4: checking gh auth${RST}"
if ! gh auth status >/dev/null 2>&1; then
    fail "gh not authenticated. Run:  gh auth login --hostname github.com --git-protocol ssh --web"
    exit 3
fi

AUTH_USER="$(gh api user --jq '.login' 2>/dev/null || echo "")"
if [ -z "$AUTH_USER" ]; then
    fail "could not determine authenticated user via 'gh api user'"
    exit 4
fi

if [ "$AUTH_USER" != "$EXPECTED_USER" ]; then
    fail "authenticated as '${AUTH_USER}', expected '${EXPECTED_USER}'."
    fail "switch accounts:  gh auth switch --user ${EXPECTED_USER}"
    fail "or re-login:      gh auth login --hostname github.com --web"
    exit 5
fi
ok "authenticated as ${AUTH_USER}"

# -------- step 3: token scopes --------
say "${CYN}==> Step 3/4: checking token scopes${RST}"
# gh auth status prints something like:  ✓ Token scopes: repo, workflow, ...
SCOPES_RAW="$(gh auth status 2>&1 | grep -i 'Token scopes' || true)"
if [ -z "$SCOPES_RAW" ]; then
    warn "could not read token scopes from 'gh auth status' output."
    warn "will attempt archive anyway and surface the real error if scope is missing."
else
    say "  ${SCOPES_RAW}"
    # archiveRepository is part of the 'repo' scope (full repo access).
    # For fine-grained PATs it must be enabled per-repo.
    if echo "$SCOPES_RAW" | grep -qE '\brepo\b'; then
        ok "classic 'repo' scope detected — archiveRepository should work."
    else
        warn "'repo' scope NOT detected on classic PAT. If using a fine-grained PAT,"
        warn "ensure 'Administration: Read and write' is granted for ${REPO_OWNER}/${REPO_NAME}."
    fi
fi

# -------- step 4: archive --------
say "${CYN}==> Step 4/4: archiving ${REPO_OWNER}/${REPO_NAME}${RST}"
say "  (GitHub GraphQL mutation: archiveRepositoryV2 / REST PATCH archived=true)"

# gh CLI does not have a top-level `gh repo archive` subcommand. We use the
# REST endpoint with the authenticated user's token.
RESP_FILE="$(mktemp -t archive_resp.XXXXXX)"
HTTP_CODE="$(
    gh api \
        --method PATCH \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -f archived=true \
        "/repos/${REPO_OWNER}/${REPO_NAME}" \
        --include \
        >"$RESP_FILE" 2>&1
    echo $?
)"

# gh api does not directly expose HTTP status. Parse it from headers.
STATUS="$(grep -i '^HTTP/' "$RESP_FILE" | tail -1 | awk '{print $2}' || echo '000')"
BODY="$(tail -n +1 "$RESP_FILE" | sed -n '/^{/,$p')"

if [ "$STATUS" = "200" ]; then
    ARCHIVED="$(printf '%s' "$BODY" | grep -o '"archived":true' || true)"
    if [ -n "$ARCHIVED" ]; then
        ok "archive successful. ${REPO_OWNER}/${REPO_NAME} is now read-only."
        ok "verify:  gh api /repos/${REPO_OWNER}/${REPO_NAME} --jq '.archived'"
        rm -f "$RESP_FILE"
        exit 0
    fi
fi

# -------- failure path --------
fail "archive call did not return 200. status=${STATUS}"
say "----- response body (tail) -----"
printf '%s\n' "$BODY" | tail -c 800
say "--------------------------------"

if grep -qi 'Resource not accessible by personal access token' "$RESP_FILE" \
   || grep -qi 'Must have admin rights' "$RESP_FILE" \
   || grep -qi 'Insufficient permissions' "$RESP_FILE"; then
    say ""
    fail "the active token cannot archive this repo."
    say ""
    say "${YLW}Two ways to fix:${RST}"
    say ""
    say "  ${GRN}A. Add the scope to the existing PAT (classic)${RST}"
    say "     1. open  https://github.com/settings/tokens"
    say "     2. click the token used by \`gh auth\`"
    say "     3. tick the 'repo' scope (it includes admin:org, public_repo,"
    say "        and the archive mutation)."
    say "     4. save, then on this machine re-auth:"
    say "          gh auth login --hostname github.com --web   (or)"
    say "          gh auth refresh --scopes repo"
    say "     5. re-run:  bash archive_trading_repo.sh"
    say ""
    say "  ${GRN}B. Fine-grained PAT (recommended if you only touch this repo)${RST}"
    say "     1. open  https://github.com/settings/personal-access-tokens/new"
    say "     2. Resource owner: ${REPO_OWNER}"
    say "     3. Repository access: 'Only select repositories' → ${REPO_OWNER}/${REPO_NAME}"
    say "     4. Permissions → Repository → Administration: Read and write"
    say "     5. generate, copy token, then:"
    say "          gh auth login --hostname github.com --with-token   (paste token)"
    say "     6. re-run:  bash archive_trading_repo.sh"
    say ""
    say "  ${GRN}C. Web UI fallback (no token work needed)${RST}"
    say "     open  https://github.com/${REPO_OWNER}/${REPO_NAME}/settings"
    say "     scroll to 'Danger Zone' → 'Archive this repository' → follow prompts."
    say "     full step-by-step in:  archive_trading_repo.md"
fi

rm -f "$RESP_FILE"
exit 6
