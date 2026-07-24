# Archive `he-mark-qinglong/trading` — README

This file is the human-run fallback for `archive_trading_repo.sh`. Use it when
the PAT does not have `archiveRepository` permission (HTTP 403 "Resource not
accessible by personal access token") and you cannot patch the token from a CLI.

Two paths below:

- **Path A** — fix the token (do this once, then the script works).
- **Path B** — web UI click-through (no token work, one-shot).

---

## Path A — fix the PAT, then run the script

### A1. Classic PAT (typical case for `gh auth`)

1. Open https://github.com/settings/tokens
2. Click the token used by `gh` on this box
   (find it with `gh auth status` — it prints the token's account, not the
   token value itself).
3. Tick **`repo`** (this single scope covers `admin:org`, `public_repo`,
   `repo:invite`, and the `archiveRepository` mutation). Save.
4. Refresh the token locally:
   ```bash
   gh auth refresh --scopes repo
   ```
   If that fails because the token isn't yours to refresh, re-login:
   ```bash
   gh auth login --hostname github.com --web
   ```
5. Run the script:
   ```bash
   bash /Users/mark/multica/quant-loop/research/archive_trading_repo.sh
   ```

### A2. Fine-grained PAT (only if you already use one)

1. Open https://github.com/settings/personal-access-tokens/new
2. **Resource owner**: `he-mark-qinglong`
3. **Repository access**: `Only select repositories`
   → pick `he-mark-qinglong/trading`.
4. **Permissions → Repository**:
   - **Administration**: `Read and write` (required — this *is* the
     `archiveRepository` permission).
5. Generate, copy token, then on this box:
   ```bash
   gh auth login --hostname github.com --with-token
   # (paste token, Ctrl-D)
   ```
6. Run the script:
   ```bash
   bash /Users/mark/multica/quant-loop/research/archive_trading_repo.sh
   ```

---

## Path B — web UI, exact clicks (no token change)

1. Sign in to GitHub as `he-mark-qinglong`
   (https://github.com/login → enter credentials / passkey).
2. Navigate to the repo:
   https://github.com/he-mark-qinglong/trading
3. Click the **Settings** tab (gear icon, right side of the tab bar — only
   visible to repo admins).
4. Scroll all the way down the **General** page to the section labeled
   **Danger Zone** (red outline).
5. In the **"Archive this repository"** row, click **"Archive this repository"**.
6. GitHub asks you to type the repo full name to confirm:
   - type: `he-mark-qinglong/trading`
   - click **"I understand the consequences, archive this repository"**.
7. Wait for the success banner. The repo banner will change to
   *"This repository has been archived and is now read-only."*

### What "archived" means

- All issues, PRs, comments, releases, milestones, labels are **frozen**.
- Pushes are blocked. Anyone with read access can still clone/fork.
- Stars, watches, notifications, and the activity graph continue to work.
- The repo is hidden from GitHub's "New issue" prompts and from
  recommendations, but the URL still resolves.

### Un-archive (only if you archived the wrong one)

Settings → Danger Zone → **Unarchive this repository**.
Same auth path; no token scope change needed to undo.

---

## Verify it worked

After either path, sanity-check from any box:

```bash
gh api /repos/he-mark-qinglong/trading --jq '.archived'
# expected output:  true
```

Or open the repo page in a browser — the banner should say
*"Public archive. Read-only."* in yellow at the top.

---

## Notes / gotchas

- **Do not delete** the repo. Archive = reversible. Delete = not (at least not
  from the web UI for non-forks).
- If the script's `gh api /repos/.../trading` returns 404 before archive, the
  authenticated user is wrong — see Step 2 of the script.
- If GitHub shows "Two-factor authentication required" during the web path,
  finish 2FA first; archive is gated on full admin auth.
- `gh` does **not** have a `gh repo archive` subcommand in any version as of
  2.51. The script uses the REST PATCH endpoint directly. The `-f archived=true`
  flag is the documented mutation.
