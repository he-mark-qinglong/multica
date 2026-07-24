# .100 sshd diagnostic checklist

Run top-to-bottom on a fresh SSH session as `smark` from `.105` (or any peer).
All commands assume `sudo` is available; if not, drop the prefix on the
sections that only need `~/.ssh/` files.

**Target host**: `192.168.0.100` (user `smark`)
**Config paths**: `/etc/ssh/sshd_config` (canonical), and drop-ins in
`/etc/ssh/sshd_config.d/*.conf` (common on Debian/Ubuntu ≥22.04 and
RHEL/Fedora ≥9 — those are read in lexical order, **last value wins** for any
key, so a drop-in can silently override the main file). We check both.

**Hypothesis ranking** (most → least likely given typical flakes):
1. `ClientAliveInterval` × `ClientAliveCountMax` too aggressive (default
   `0`/`3` is fine, but a sysadmin may have lowered `ClientAliveInterval` to
   `15` or `30` for some other service — that punishes idle sessions).
2. `MaxStartups` too low (default `10:30:60`, but custom `3:10:10` is common
   and bites under parallel cron).
3. Auth rate limit kicking in — either `pam_faillock`, `ufw`/`nftables`
   connection-rate rule, or `sshguard`/`fail2ban` on the box.
4. systemd shim — `systemd-logind` killing sessions on user-switch/idle.
5. OOM kill of `sshd` (look in `dmesg`/`journald`).

---

## 1. Pre-flight (from `.105`, no `sudo` needed)

```bash
# 1.1 — confirm key fingerprint on client matches what the box should have
ssh-keygen -lf ~/.ssh/id_ed25519.pub
ssh-keygen -lf ~/.ssh/id_rsa.pub 2>/dev/null || true
# what to look for: SHA256 string. Compare against last-known good.

# 1.2 — check ssh-agent has the key loaded (if you use one)
ssh-add -l
# what to look for: 1+ keys listed. If "The agent has no identities", run
#   ssh-add ~/.ssh/id_ed25519   (or whichever) and re-test.

# 1.3 — last successful login from this account (run on .100 once you're in)
last -n 20 smark
lastb -n 20 smark 2>/dev/null || true   # bad logins; may need sudo
# what to look for:
#   - 'smark' appears in 'last' recently → key auth has worked before.
#   - 'lastb' empty → not being brute-forced.
#   - 'crash' or 'gone' as termination → sshd killed the session, not you.
```

```bash
# 1.4 — fastest connectivity smoke test BEFORE opening a long session
ssh -o BatchMode=yes -o ConnectTimeout=5 smark@192.168.0.100 echo ok
# what to look for:
#   'ok'        → sshd is up, key auth works, the problem is mid-session.
#   'Permission denied (publickey)' → key problem, jump to §3.
#   'Connection refused'           → sshd not listening, jump to §4.
#   'Connection timed out'         → firewall/iptables, jump to §4.
```

---

## 2. System checks (on `.100`, `sudo`)

```bash
# 2.1 — is sshd running and enabled?
sudo systemctl status sshd --no-pager
# what to look for:
#   Active: active (running)              ← healthy
#   Active: failed                       ← restart loop, see 2.4
#   Loaded: loaded (/lib/systemd/system/ssh.service; enabled; ...)
#                                       ← enabled at boot is what we want.
#   "Condition: start condition failed" → ConditionPathExists etc; rarely the issue.

# 2.2 — full effective sshd config dump (what's actually being used)
sudo sshd -T | grep -iE '^(clientalive|clientalivecountmax|tcpkeepalive|maxstartups|maxsessions|logingrace|permitroot|passwordauth|usepam|allowusers|allowgroups|denyusers|denygroups|port|listenaddress) '
# what to look for:
#   ClientAliveInterval 0  ClientAliveCountMax 3   ← defaults; benign.
#   ClientAliveInterval 15 ClientAliveCountMax 2    ← aggressive, fixes §6.
#   MaxStartups 3:30:10                              ← low, fixes §6.
#   MaxSessions 1                                    ← too low for tmux+rsync.

# 2.3 — does the dump show any unexpected drop-ins?
sudo sshd -T | grep -i 'include\|configfile\|filename'
# (different sshd versions expose this differently; the line is informational.)
# also list drop-ins directly:
sudo ls -la /etc/ssh/sshd_config.d/ 2>/dev/null
# what to look for: any *.conf file in there that might override the main config.

# 2.4 — journal: last 200 sshd lines
sudo journalctl -u sshd -n 200 --no-pager
# what to look for:
#   'Accepted publickey for smark from ...'              ← success baseline.
#   'Connection closed by ... [preauth]'                 ← client gave up; key issue.
#   'Disconnected from user smark ... [time elapsed]'    ← clean end.
#   'Received disconnect from ... [authenticated]'      ← client sent goodbye.
#   'fatal: Read from socket failed: Connection reset'  ← network/timeout.
#   'fatal: sshd: PID X killed by SIG... '               ← OOM (then jump to §5).
#   'error: kex_exchange_identification: ...'           ← cipher mismatch / MitM.
#   'pam_unix(sshd:session): session opened/closed for smark' — pam happy.

# 2.5 — start time + uptime of sshd (for "did it restart today?" check)
sudo systemctl show sshd -p ActiveEnterTimestamp,NAcceptedConnections,NRunning,sysuptime,ActiveState,SubState --no-pager
# what to look for:
#   ActiveEnterTimestamp in the last hour + NAcceptedConnections = 0  → just
#     restarted and nobody's connected, suggests it's crash-looping.
```

---

## 3. Config audit (`/etc/ssh/sshd_config` and drop-ins)

```bash
# 3.1 — main file: every keepalive / limit directive, with line numbers
sudo grep -nEi \
  '^\s*(ClientAliveInterval|ClientAliveCountMax|TCPKeepAlive|MaxStartups|MaxSessions|LoginGraceTime|PermitRootLogin|PasswordAuthentication|PubkeyAuthentication|ChallengeResponseAuthentication|KbdInteractiveAuthentication|UsePAM|AllowUsers|AllowGroups|DenyUsers|DenyGroups|Port|ListenAddress|AddressFamily)\b' \
  /etc/ssh/sshd_config
# what to look for:
#   - '#' in front means default applies (key/value pairs without a setting).
#   - any line that disagrees with §2.2 was overridden by a drop-in (see 3.2).

# 3.2 — drop-ins: same grep, every .conf file
sudo grep -nREi \
  '^\s*(ClientAliveInterval|ClientAliveCountMax|TCPKeepAlive|MaxStartups|MaxSessions|LoginGraceTime|PermitRootLogin|PasswordAuthentication|PubkeyAuthentication|UsePAM|AllowUsers|AllowGroups|DenyUsers|DenyGroups)\b' \
  /etc/ssh/sshd_config.d/ 2>/dev/null || echo "(no drop-in dir)"
# what to look for:
#   Anything here OVERRIDES the main file. Lexical order, last-wins.
#   e.g. /etc/ssh/sshd_config.d/99-hardening.conf with
#       ClientAliveInterval 15
#       ClientAliveCountMax 2
#   is a smoking gun for idle disconnects.

# 3.3 — config syntax validity (no reload, just parse)
sudo sshd -t
# what to look for: silent exit + no output = config parses cleanly.
# Any 'Bad configuration option' / 'line N: missing argument' → fix before reload.
```

---

## 4. Network / firewall checks (on `.100`)

```bash
# 4.1 — sshd actually listening on 22?
sudo ss -tlnp | grep -E ':22\b|sshd'
# expected:  LISTEN  0  128  0.0.0.0:22  0.0.0.0:*  users:(("sshd",pid=...,fd=...))
# bad signs:  no line at all → sshd down (jump to §2.1).
#             LISTEN only on 127.0.0.1 → remote access blocked by ListenAddress.

# 4.2 — same check, alternative tool
sudo netstat -tlnp 2>/dev/null | grep -E ':22\b|sshd' || true

# 4.3 — firewall rules touching port 22
sudo iptables -L INPUT -nv --line-numbers 2>/dev/null || true
sudo nft list ruleset 2>/dev/null | grep -iE 'ssh|dport 22' || true
# what to look for:
#   A 'DROP' or 'REJECT' rule that hits tcp dpt:22 from your .105 source IP.
#   A recent 'recent:SET' rule from fail2ban/sshguard → that's your rate limit.

# 4.4 — fail2ban / sshguard presence
sudo fail2ban-client status sshd 2>/dev/null || echo "(no fail2ban)"
sudo systemctl status sshguard --no-pager 2>/dev/null || echo "(no sshguard)"
# what to look for: if present, check 'Currently banned' and your IP:
#   sudo fail2ban-client status sshd | grep -A 5 'Banned IP list'
#   sudo fail2ban-client set sshd unbanip 192.168.0.105   ← unban yourself if needed.

# 4.5 — rate limit / conntrack pressure on port 22
sudo ss -tn 'sport = :22 or dport = :22' | wc -l
sudo ss -tn 'sport = :22 or dport = :22' state time-wait | wc -l
# what to look for: thousands of TIME_WAIT on 22 → MaxStartups/conntrack
# exhaustion, that's §6.
```

---

## 5. Crash / OOM forensics

```bash
# 5.1 — kernel ring around sshd in the last 24h
sudo dmesg --since='24 hours ago' --ctime 2>/dev/null \
  | grep -iE 'sshd|oom|out of memory|killed process' || echo "(no sshd/OOM in dmesg)"

# 5.2 — systemd-coredump listing for sshd
sudo coredumpctl list --since='24 hours ago' 2>/dev/null \
  | grep -i sshd || echo "(no sshd coredumps in 24h)"
# if a core is listed:
#   sudo coredumpctl info <PID-or-TIME>
#   sudo coredumpctl debug --no-pager   ← then 'bt' inside gdb.

# 5.3 — OOM-kill log via journald
sudo journalctl -k --since='24 hours ago' --no-pager | grep -iE 'oom|killed' || echo "(no OOM)"

# 5.4 — sshd restart count today (sanity)
sudo journalctl -u sshd --since '00:00' --no-pager \
  | grep -cE 'Started sshd|Stopped sshd|relocated to' || true
# what to look for: a number > 5 → it's crash-looping.
```

---

## 6. Test scenarios

### 6.1 — verbose single-shot from `.105`

```bash
ssh -vvv -o ConnectTimeout=10 -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 smark@192.168.0.100 'echo connected && uptime'
# what to look for in the trace:
#   debug1: Authentication succeeded (publickey).     ← auth fine.
#   debug2: channel 0: open confirm rwindow ...       ← session fine.
#   "Connection reset by peer" mid-stream             ← server killed session.
#   "Write failed: Broken pipe"                       ← keepalive missed, §6 fix.
# Save full log for posterity:
#   ssh -vvv ... 2>&1 | tee /tmp/ssh_verbose_$(date +%s).log
```

### 6.2 — parallel connection storm (probes MaxStartups)

```bash
# from .105 — open 12 simultaneous sessions
for i in $(seq 1 12); do
  ssh -o BatchMode=yes -o ConnectTimeout=5 \
      smark@192.168.0.100 'sleep 5' &
done
wait
# what to look for:
#   10 succeed, 2 fail with "ssh_exchange_identification: Connection closed
#   by remote host" → MaxStartups hit (default drops start after 10 unauth'd).
```

### 6.3 — idle-disconnect probe (probes ClientAliveInterval × Count)

```bash
# from .105 — open one session, do nothing for 5 minutes
ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    smark@192.168.0.100 'date; sleep 300; date; echo done'
# what to look for:
#   if it dies at ~60s and ServerAliveInterval was unset (default 0) →
#   server's ClientAliveInterval is the culprit, jump to §6 fix below.
#   if it survives 5 min → keepalive is healthy.
```

### 6.4 — reconnect-after-drop simulation

```bash
# from .105 — force one TCP RST to see how long reconnect takes
ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=5 \
    -o ServerAliveCountMax=1 smark@192.168.0.100 'echo hi'
# disconnect Wi-Fi on .105 for 10s, then reconnect
# what to look for: ssh client prints "Connection to ... closed" and exits,
# because ServerAliveCountMax=1 was set low. Recovery on next invocation
# should be instant; if not, §4 problem.
```

---

## 7. Common fixes (apply in this order, test after each)

### 7.1 — raise keepalive (idle drop fix)

```bash
sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak.$(date +%Y%m%d-%H%M%S)
sudo tee /etc/ssh/sshd_config.d/99-keepalive.conf >/dev/null <<'EOF'
# raise the server-side keepalive so .105 sessions survive 5 min idle
ClientAliveInterval 300
ClientAliveCountMax 2
TCPKeepAlive yes
EOF
sudo sshd -t && sudo systemctl reload sshd
# verify new effective values:
sudo sshd -T | grep -i clientalive
# expected:  clientaliveinterval 300 ; clientalivecountmax 2
```

### 7.2 — raise MaxStartups (parallel-session fix)

```bash
sudo tee /etc/ssh/sshd_config.d/98-startups.conf >/dev/null <<'EOF'
# start:drop:max — accept 10 unauth'd, drop prob starting at 30/100 above 10,
# hard cap 60.
MaxStartups 10:30:60
MaxSessions 20
LoginGraceTime 30s
EOF
sudo sshd -t && sudo systemctl reload sshd
sudo sshd -T | grep -iE 'maxstartups|maxsessions'
# expected: maxstartups 10:30:60 ; maxsessions 20
```

### 7.3 — allow key-only rootless + no password

```bash
sudo tee /etc/ssh/sshd_config.d/97-auth.conf >/dev/null <<'EOF'
PasswordAuthentication no
ChallengeResponseAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
EOF
sudo sshd -t && sudo systemctl reload sshd
# DANGER: do this only if your key is on the box AND you've tested it via
# a second open session. Locking yourself out is a service call.
```

### 7.4 — disable pam_faillock / fail2ban if they're wrong

```bash
# see who's banning you:
sudo fail2ban-client status sshd
sudo fail2ban-client set sshd unbanip 192.168.0.105
# to disable (only after we've identified it as the cause):
sudo fail2ban-client stop sshd
sudo systemctl disable --now fail2ban
```

**Always reload, never restart.** `reload` re-reads config without dropping
existing sessions:

```bash
sudo sshd -t && sudo systemctl reload sshd
# if sshd -t complains, fix syntax first; the reload will be a no-op.
```

---

## 8. After-fix verification

The whole point of this checklist is to stop the sshd flap that breaks
`multica-minimax-tunnel.service`. After applying fixes from §7:

```bash
# 8.1 — on .100, restart the tunnel service
sudo systemctl restart multica-minimax-tunnel.service
sudo systemctl status multica-minimax-tunnel.service --no-pager

# 8.2 — let it run for 10 minutes; tail logs in another window
sudo journalctl -u multica-minimax-tunnel.service -f --no-pager

# 8.3 — on .100, watch sshd session churn in parallel
sudo journalctl -u sshd -f --no-pager | grep --line-buffered -E 'Accepted|Disconnected|Received disconnect'
# what to look for:
#   'Disconnected ... [time elapsed: X]' — X should be ≥ 60s, not 0.
#   No 'fatal' / 'Connection reset' messages.
#   Steady 'Accepted publickey' lines (not bursty then silent).

# 8.4 — on .100, watch active sshd processes
watch -n 5 'ps -ef | grep "[s]shd:.*smark" | wc -l'
# what to look for: 1 (single tunnel session). If 0, tunnel dropped.

# 8.5 — pass/fail criteria for "we fixed it"
#   - tunnel service stays 'active (running)' for 10 min.
#   - journald shows ≤1 disconnect over 10 min.
#   - smark can `ssh smark@192.168.0.100 'echo hi'` from .105, kill the
#     session, and re-establish within 1 second.
# If any of those fail, re-run §2.4 with -n 1000 and look for the new failure
# pattern (it'll be a different one — this checklist fixes the common ones).
```

---

## Quick decision tree

| Symptom on .105                              | Jump to |
|----------------------------------------------|---------|
| "Connection refused" / "timed out"           | §4      |
| "Permission denied (publickey)"              | §1.1, §3 (PubkeyAuthentication) |
| Connects, drops in <60s of idle              | §6.3 + §7.1 |
| Connects, drops on parallel `scp`/`rsync`    | §6.2 + §7.2 |
| "kex_exchange_identification: ..."           | §2.2 (ciphers) + §4.3 |
| Auth succeeds, then "Connection reset"       | §5 (OOM/crash) |
| Works from .104, fails from .105             | §4.3 (per-IP block) |

---

## Appendix — one-shot collection script

If you want to grab everything for an offline review:

```bash
sudo bash -c '
  set +e
  out=/tmp/sshd_diag_$(hostname)_$(date +%Y%m%d-%H%M%S)
  mkdir -p "$out"
  systemctl status sshd --no-pager                       > "$out/systemctl_status.txt"
  sshd -T                                                > "$out/sshd_T.txt" 2>&1
  journalctl -u sshd -n 1000 --no-pager                  > "$out/journal_sshd.txt"
  journalctl -u sshd --since "24 hours ago" --no-pager  >> "$out/journal_sshd.txt"
  cp /etc/ssh/sshd_config                                "$out/sshd_config.main"
  cp -r /etc/ssh/sshd_config.d                           "$out/sshd_config.d" 2>/dev/null
  iptables -L INPUT -nv                                  > "$out/iptables.txt" 2>&1
  ss -tlnp                                               > "$out/ss_listen.txt"
  ss -tn "sport = :22 or dport = :22"                    > "$out/ss_22.txt"
  dmesg --since "24 hours ago" --ctime                   > "$out/dmesg_24h.txt" 2>&1
  coredumpctl list --since "24 hours ago"                > "$out/coredumps_24h.txt" 2>&1
  last -n 50 smark                                       > "$out/last_smark.txt" 2>&1
  lastb -n 50 smark                                      > "$out/lastb_smark.txt" 2>&1
  tar -czf "${out}.tar.gz" -C /tmp "$(basename "$out")"
  echo "wrote: ${out}.tar.gz"
'
# scp the tarball to .105 for review:
scp /tmp/sshd_diag_*.tar.gz smark@192.168.0.105:/tmp/
```

That's the whole loop. If the tarball is >10 MB, it's almost certainly full of
journal noise — pipe through `journalctl -u sshd -n 1000` only, not `--since`.
