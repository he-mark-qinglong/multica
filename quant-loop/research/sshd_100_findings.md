# .100 sshd diagnostic findings — 2026-07-20

**Target**: `smark@192.168.0.100` (smark's MacBook Air, macOS 26.5 Darwin 25.5.0)
**Source**: `smark@192.168.0.105` (Ubuntu 24.04, kernel 6.17)
**Checklist run from**: `/Users/mark/multica/quant-loop/research/sshd_100_diagnostic.md`
**Diagnostic style**: READ-ONLY. No config edits, no service restarts. (Parent agent restricted to diagnosis.)

---

## Topology correction (up-front)

The checklist was written assuming `.100` is systemd-Linux. **It is not.** `.100` is
macOS — `sshd` runs under `launchd` socket activation (`/System/Library/LaunchDaemons/ssh.plist`),
not as a long-lived daemon. macOS equivalents were used throughout:

| Checklist tool | macOS adapter used |
|---|---|
| `systemctl status sshd` | `launchctl print system/com.openssh.sshd` |
| `journalctl -u sshd` | `log show --predicate 'process == "sshd"' --last 24h` |
| `iptables` / `nft` | ALF state (`socketfilterfw --getglobalstate`), `/etc/pf.conf` |
| `dmesg` | `log show` (no dmesg equivalent on macOS) |
| `coredumpctl list` | `~/Library/Logs/DiagnosticReports/` + `/Library/Logs/DiagnosticReports/` |
| `fail2ban-client` | not installed (no fail2ban on .100) |
| `sudo sshd -T` | blocked by sudo password requirement on .100 |

**`.105` was also probed** — the symptom is observed from `.105` and the duplicate-tunnel
root cause lives on `.105`.

---

## Symptom matrix

| Check | Result | Notes |
|---|---|---|
| `.100` reachable (ping) | ✅ 0% loss, 3.7 ms avg | network healthy between `.105` and `.100` |
| `.100` sshd listening on 22 | ✅ `launchd:1` holds `tcp4 *.22` + `tcp6 ::.22` | socket-activated via `ssh.plist` `Sockets/Listeners` |
| `.100` sshd actual process | ✅ running, 1 long-lived session 9 h 20 m | corresponds to autossh 752106 child (PID 1360363) on `.105` |
| `.100` sshd config | ✅ vanilla macOS defaults | all keepalive / limits directives commented out |
| `.100` drop-ins | ✅ only `100-macos.conf` | `UsePAM yes` + `AcceptEnv LANG LC_*` + `Include /etc/ssh/crypto.conf` |
| `.100` sshd crashes / OOM | ✅ none in 7 d | empty `~/Library/Logs/DiagnosticReports`, no `killed by signal` log |
| Loopback SSH on `.100` | ✅ `Exit status 0`, 3 172 / 3 192 bytes | sshd handles local auth fine |
| Parallel-12 storm `.105 → .100` | ✅ all 12 succeed in 0 s | **MaxStartups not hit**, default `10:30:100` healthy |
| `.100` TIME_WAIT on 22 | ✅ 0 | no connection churn |
| `.100` audit logs (`~/audit/`) | ❌ **all empty** since Jun 6 | `ssh-audit-daily.sh` grep filter doesn't match macOS log format |
| `.100` sshd **"Accepted publickey"** log entries | ❌ **0 in 24 h**, 0 in 7 d | macOS sshd logs only `libsystem_info.dylib` chatter, no auth events |
| `.100` sshd **"Disconnected from user"** log entries | ❌ 0 in 24 h, 0 in 7 d | sshd is not terminating sessions; clients are |
| `.100` ALF firewall | ✅ disabled (State = 0) | not the cause of "port 22 unreachable" |
| `.100` power settings | ⚠️ `sleep=1`, `networkoversleep=0`, `powernap=1`, `hibernatemode=3` | sleep drops TCP connections |
| `.105 → .100` SSH attempt (live) | ✅ 3/3 success, <2 s each | works fine for one-shot commands |
| `.105` autossh processes | ❌ **TWO duplicates** for `.100` | PIDs 752106 + 1355918, both `-L 18091:10.6.0.91:80 smark@192.168.0.100` |

---

## Top 3 root causes (ranked by confidence)

### 1. [85 %] Duplicate autossh on `.105` — one in restart loop

**Evidence** (from `ps -ef` on `.105`):
```
smark  752106  3267  0 Jul19  autossh -M 0 -N -o ServerAliveInterval=30  -L 18091:10.6.0.91:80 smark@192.168.0.100
smark 1355918     1  0 Jul19  autossh -M 0 -N -o ServerAliveInterval=15  -L 18091:10.6.0.91:80 smark@192.168.0.100
```

- `752106` has a stable ssh child `PID 1360363` (elapsed 9 h 20 m, holds `127.0.0.1:18091`).
- `1355918` has **no ssh child** at sample time — confirmed restart loop (see `ps --ppid 1355918`).
- Both want `-L 18091:...`; only one wins the bind. The other exits via `ExitOnForwardFailure=yes` and autossh restarts it in a tight loop.
- 346 unique sshd PIDs on `.100` in 24 h ≈ 14/h, matching the ~4-line-per-cycle log entries at intervals of ~2-10 min during baseline.

**Why it matches "sshd child dies every ~2 min"**: from `.100`'s viewpoint, every restart of
`.105`'s ssh client looks like a brand-new TCP connection — `launchd` spawns `sshd-keygen-wrapper`
→ `sshd` (preauth privsep) → auth → session → EOF → exit. The macOS sshd per-connection lifecycle
is exactly what the observer calls "sshd child dies".

**Single fix**: `kill 1355918` on `.105` (preserve 752106). Eliminates the duplicate's
restart loop immediately. No change needed on `.100`.

### 2. [75 %] macOS sleep on `.100` drops TCP connections

**Evidence** (from `pmset -g`):
```
sleep              1   (sleep prevented by powerd, coreaudiod, coreaudiod)
powernap           1
networkoversleep   0   ← connections torn down on sleep
hibernatemode      3   ← hibernate + sleep
tcpkeepalive       1
```

- `networkoversleep=0` means **existing TCP connections are RST'd when the lid closes / sleep fires**.
- `ClientAliveInterval=0` (default in `.100`'s sshd) → server never sends keepalive. Only OS-level TCP keepalive (typically 7200 s) detects dead peers.
- On wake, `launchd` re-binds port 22, but the prior session's `sshd-session` (the 9 h 20 m one) survives only if the client reconnect succeeds during wake-up.
- This explains the "port 22 sometimes unreachable from `.105` even when `.100` is pingable" symptom — `.100` is awake enough to answer ping (Bonjour / mDNS) but the sshd-keygen-wrapper state machine is mid-respawn.

**Fix options** (pick one, all need sudo on `.100`):
- Disable sleep while plugged in: `sudo pmset -c disablesleep 1`
- Keep network alive in sleep: `sudo pmset -a networkoversleep 1` (then sshd stays responsive, but battery cost)
- Add server-side keepalive (recommended, smallest blast radius): drop a file `/etc/ssh/sshd_config.d/01-keepalive.conf` with `ClientAliveInterval 60` / `ClientAliveCountMax 3`. No restart needed if you `sudo launchctl kickstart -k system/com.openssh.sshd` after `sudo sshd -t`.

### 3. [50 %] `.100` sshd emits zero auth/disconnect log entries

**Evidence** (from `log show --predicate 'process == "sshd"' --last 7d`):
- Only 3 distinct event-message templates: `Retrieve User by ID`, `Retrieve User by Name`, `[com.apple.network.libinfo:si_destination_compare] send failed: Invalid argument`.
- **Zero** `Accepted publickey` / `Accepted password` / `Disconnected from user` / `fatal` / `killed by signal` in 7 d.
- The `com.apple.sshd` subsystem is empty. `sshd-keygen-wrapper` is empty.

This is not the cause of the symptom, but it makes diagnosis impossible from `.100` alone.
Possible reasons: macOS OpenSSH 10.2p1 sandboxing, `LogLevel` interpretation difference, or
auth events routed through `com.apple.system.opendirectory` instead. The audit-script grep
pattern (`Accepted|Failed|session opened|...`) is dead — `~/audit/sshd-*.log` files all
contain only the `--- collected at ... ---` marker, no events. **Operational hygiene issue.**

**Fix**: re-author `ssh-audit-daily.sh` to query `log show --predicate 'process == "sshd" AND eventMessage CONTAINS "Accepted"' --last 24h` and similar — current pattern misses all macOS logs.

---

## Other observations (informational, not root causes)

- `com.smark.ssh-audit` LaunchAgent runs `~/bin/ssh-audit-daily.sh` daily at 09:00 — logs to `/tmp/ssh-audit-daily.{out,err}` (both empty today).
- `com.ssh.tunnel` LaunchAgent runs `/Users/smark/ssh-tunnel.sh`, an outbound SOCKS proxy loop (`ssh -f -N -D 1080 ubuntu@43.167.9.219` to Tokyo), checks every 30 s, restarts on drop. Not involved with `.100` sshd.
- `.100`'s `com.openssh.sshd` plist has `<key>Disabled</key><true/>` but is still loaded because `Sockets/Listeners` keeps it socket-activated. This is **standard** macOS behavior — don't `launchctl unload` it.
- `.105` also runs a Tokyo-bound tunnel: `autossh -R 0.0.0.0:{8080,3000,3210,18081}:127.0.0.1:{...} ubuntu@43.167.9.219` (PID 1958482, started today). Independent of the `.100` issue.

---

## Recommended fix (single change — kill the duplicate)

On `.105`, as smark:
```bash
# verify first — make sure 752106 (with the live ssh child) is the one we keep
ps -p 752106 -o pid,etime,cmd
ps -p 1360363 -o pid,etime,cmd   # should show 9 h 20 m old ssh
# kill the orphan restart-loop autossh
kill 1355918
# confirm only one autossh to .100 remains
ps -ef | grep "[a]utossh.*\\.100"
# after 5 min, sshd child count on .100 should drop to 0 churn:
ssh smark@192.168.0.100 'ps -axo pid,etime,command | grep "[s]shd-session: smark"'
```

**Expected outcome**:
- `.100` sshd PIDs per hour drops from ~14 to ~0 (only the 1 persistent session remains).
- No more "sshd child dies every ~2 min" symptom from `.105`.
- Local port `18091` on `.105` continues to work (now only bound by 752106's ssh).

**If the symptom persists after step 1**, the macOS-sleep root cause is the next suspect;
apply `ClientAliveInterval 60` + `ClientAliveCountMax 3` in `/etc/ssh/sshd_config.d/01-keepalive.conf`
and `sudo launchctl kickstart -k system/com.openssh.sshd` (no full restart, preserves
existing sessions).

---

## Verification commands (for after-the-fix)

```bash
# 1. on .105: confirm single autossh
ps -ef | grep '[a]utossh.*\.100'

# 2. on .105: confirm 18091 bound and reachable
ss -tlnp | grep 18091
curl --max-time 3 -s http://127.0.0.1:18091/ -o /dev/null -w '%{http_code}\n'

# 3. on .100: sshd churn over 5 min
ssh smark@192.168.0.100 'log show --predicate "process == \"sshd\"" --last 5m --style compact 2>&1 | grep -c "sshd\["'

# 4. cross-host: stable long-lived sshd-session
ssh smark@192.168.0.100 'ps -axo pid,etime,command | grep "[s]shd-session" | head -5'
```

Pass criteria: (1) one autossh, (2) 18091 returns HTTP code, (3) sshd log line count < 20/5 min, (4) the persistent sshd-session elapsed time grows monotonically.

---

## Files referenced

- Local: `/Users/mark/multica/quant-loop/research/sshd_100_diagnostic.md` (checklist, source-of-truth)
- `.100`: `/etc/ssh/sshd_config`, `/etc/ssh/sshd_config.d/100-macos.conf`, `/Users/smark/ssh-tunnel.sh`, `~/bin/ssh-audit-daily.sh`, `/Library/LaunchDaemons/com.openssh.sshd` is `ssh.plist` (system, read-only)
- `.105`: two duplicate `autossh` PIDs (752106 keep, 1355918 kill), Tokyo-bound `autossh` 1958482 (unrelated)

## No changes made

This was a pure diagnostic run. No files on `.100` or `.105` were modified. No services were restarted. No config edits.
