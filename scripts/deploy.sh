#!/usr/bin/env bash
# multica server deploy pipeline — build → upload → backup → migrate → swap → restart → verify.
#
# Run from a machine with the repo working tree and SSH access to the deploy host:
#   scripts/deploy.sh [server|cli|all]     (default: all)
#
# Properties:
#   - Cross-builds linux/amd64 binaries locally (CGO_ENABLED=0), never builds on the host.
#   - Backs up the Postgres DB (docker exec pg_dump) before migrating.
#   - Keeps the previous server binary as server/bin/server.bak-<stamp> for rollback.
#   - On post-swap verify failure, restores the backup binary and relaunches automatically.
#   - Restarts the daemon ONLY when no tasks are running/dispatched; otherwise skips with a notice.
#
# Env overrides: DEPLOY_HOST (default smark@192.168.0.105), REMOTE_DIR (default /home/smark/multica)
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-smark@192.168.0.105}"
REMOTE_DIR="${REMOTE_DIR:-/home/smark/multica}"
DB_CONTAINER="${DB_CONTAINER:-multica-postgres-1}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
COMPONENTS="${1:-all}"
DIST="$REPO_ROOT/dist/deploy-$STAMP"

log() { printf '\033[1m[deploy %s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }

# ---------------------------------------------------------------- web only
# Source sync (no --delete, --update so cron-written files on the host win) →
# pnpm install → next build → restart `pnpm start` → verify :3000.
if [[ "$COMPONENTS" == "web" ]]; then
  log "syncing source tree to $DEPLOY_HOST (no --delete, --update)"
  rsync -auz --exclude='.git' --exclude='node_modules' --exclude='data' --exclude='dist' \
    --exclude='test-results' --exclude='.next' --exclude='.turbo' --exclude='.env' \
    --exclude='server/bin' --exclude='.deploy-*' --exclude='*.log' --exclude='.DS_Store' \
    -e ssh "$REPO_ROOT/" "$DEPLOY_HOST:$REMOTE_DIR/"
  log "building web on host (this takes a few minutes)"
  ssh -o ConnectTimeout=5 "$DEPLOY_HOST" REMOTE_DIR="$REMOTE_DIR" bash -s <<'REMOTE_WEB'
set -euo pipefail
cd "$REMOTE_DIR"
export PATH="$HOME/.local/share/pnpm:$PATH"
say() { printf '[remote %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
say "pnpm install --frozen-lockfile"
pnpm install --frozen-lockfile
say "next build (apps/web)"
pnpm --filter @multica/web build
say "restarting web (next start)"
pkill -f 'next start' 2>/dev/null || true
pkill -f 'next-server' 2>/dev/null || true
for i in $(seq 1 10); do ss -tln | grep -q ':3000 ' || break; sleep 1; done
cd apps/web
nohup pnpm start >> "$HOME/multica-tunnel/web-prod.log" 2>&1 & disown
for i in $(seq 1 60); do
  if curl -sf -o /dev/null http://localhost:3000; then say "web OK on :3000"; exit 0; fi
  sleep 2
done
say "ERROR: web did not come up on :3000 — check $HOME/multica-tunnel/web-prod.log"
exit 1
REMOTE_WEB
  log "web deploy OK"
  exit 0
fi

# ---------------------------------------------------------------- build
log "building linux/amd64 binaries into $DIST"
mkdir -p "$DIST"
cd "$REPO_ROOT/server"
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o "$DIST/server"  ./cmd/server
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o "$DIST/migrate" ./cmd/migrate
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o "$DIST/multica" ./cmd/multica
log "build OK: $(ls -lh "$DIST" | awk 'NR>1{print $9" "$5}' | paste -sd', ' -)"

# ---------------------------------------------------------------- upload
log "uploading to $DEPLOY_HOST:$REMOTE_DIR/server/bin/.deploy-$STAMP"
ssh -o ConnectTimeout=5 "$DEPLOY_HOST" "mkdir -p '$REMOTE_DIR/server/bin/.deploy-$STAMP' \"\$HOME/multica-backups\""
scp -q "$DIST/server" "$DIST/migrate" "$DIST/multica" "$DEPLOY_HOST:$REMOTE_DIR/server/bin/.deploy-$STAMP/"

# Migration files travel with the deploy (the migrate tool reads them from
# disk, not from the binary). Add/update only — never delete remote files.
log "syncing migration files"
rsync -az --include='*.sql' --exclude='*' -e ssh "$REPO_ROOT/server/migrations/" "$DEPLOY_HOST:$REMOTE_DIR/server/migrations/"

# ---------------------------------------------------------------- remote
log "running remote deploy phase"
ssh -o ConnectTimeout=5 "$DEPLOY_HOST" COMPONENT="$COMPONENTS" STAMP="$STAMP" DB_CONTAINER="$DB_CONTAINER" REMOTE_DIR="$REMOTE_DIR" bash -s <<'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"
D="server/bin/.deploy-$STAMP"
say() { printf '[remote %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

# --- 1. DB backup
say "backing up postgres ($DB_CONTAINER) → ~/multica-backups/pre-deploy-$STAMP.sql.gz"
docker exec "$DB_CONTAINER" pg_dump -U multica multica | gzip > "$HOME/multica-backups/pre-deploy-$STAMP.sql.gz"
ls -lh "$HOME/multica-backups/pre-deploy-$STAMP.sql.gz" | awk '{print "  backup size: "$5}'

# --- 2. migrate up (new migrate binary carries the latest migrations)
say "applying migrations"
set -a; . ./.env; set +a
"$D/migrate" up

# --- 3. swap + restart server API
if [[ "$COMPONENT" == "server" || "$COMPONENT" == "all" ]]; then
  cp -a server/bin/server "server/bin/server.bak-$STAMP"
  install -m 755 "$D/server" server/bin/server
  OLD_PID="$(pgrep -f 'server/bin/server$' | head -1 || true)"
  say "restarting server (old pid: ${OLD_PID:-none})"
  [[ -n "$OLD_PID" ]] && kill "$OLD_PID" || true
  for i in $(seq 1 10); do [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null || break; sleep 1; done
  [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null && kill -9 "$OLD_PID" || true
  bash -c 'set -a; . ./.env; set +a; nohup ./server/bin/server >> "$HOME/multica-tunnel/backend-prod.log" 2>&1 & disown'

  rollback() {
    say "VERIFY FAILED — rolling back to server.bak-$STAMP"
    cp -a "server/bin/server.bak-$STAMP" server/bin/server
    pkill -f 'server/bin/server$' || true; sleep 2
    bash -c 'set -a; . ./.env; set +a; nohup ./server/bin/server >> "$HOME/multica-tunnel/backend-prod.log" 2>&1 & disown'
    sleep 4
    exit 1
  }

  say "waiting for /healthz"
  ok=0
  for i in $(seq 1 30); do
    if curl -sf http://localhost:8080/healthz >/tmp/healthz.json 2>/dev/null; then ok=1; break; fi
    sleep 1
  done
  [[ "$ok" == "1" ]] || rollback
  cat /tmp/healthz.json; echo
  grep -q '"migrations":"ok"' /tmp/healthz.json || rollback
  # smoke: new routes must exist (401 unauthorized, not 404)
  for route in /api/tasks /api/metrics/query /api/artifacts; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:8080$route")
    say "smoke $route → HTTP $code"
    [[ "$code" == "401" || "$code" == "200" ]] || rollback
  done
fi

# --- 4. CLI binaries
if [[ "$COMPONENT" == "cli" || "$COMPONENT" == "all" ]]; then
  say "installing new multica CLI"
  install -m 755 "$D/multica" server/bin/multica
  sudo -n cp -a /usr/local/bin/multica "/usr/local/bin/multica.bak-$STAMP"
  sudo -n install -m 755 "$D/multica" /usr/local/bin/multica
  /usr/local/bin/multica version | head -1
fi

# --- 5. daemon restart (only when idle)
RUNNING=$(docker exec "$DB_CONTAINER" psql -U multica -tAc \
  "SELECT count(*) FROM agent_task_queue WHERE status IN ('dispatched','running')")
if [[ "$RUNNING" == "0" ]]; then
  say "no tasks in flight — restarting multica-daemon"
  systemctl --user restart multica-daemon
  sleep 3
  systemctl --user is-active multica-daemon
else
  say "daemon restart SKIPPED: $RUNNING task(s) still running — new daemon binary takes effect on next natural restart"
fi
say "deploy phase complete (stamp $STAMP)"
REMOTE

log "deploy OK — stamp $STAMP"
log "rollback artifacts on host: server/bin/server.bak-$STAMP, /usr/local/bin/multica.bak-$STAMP, ~/multica-backups/pre-deploy-$STAMP.sql.gz"
