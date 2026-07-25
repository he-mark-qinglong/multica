# INT-06 — .105 Data-In-Place Precheck (gate for W4-T10/T12/T14)

- Issue: SMA-36529
- Card source: `docs/plans/infra-sprint-2026-07-25/round2/w5-s5-integration.md` §5
- Run: 2026-07-25T21:16+08 (multica-code)
- Verdict: **GO-105**

## Acceptance: files in place on .105

`ssh smark@192.168.0.105 'ls -la /home/smark/multica/quant-loop/data/perp_1m/BTCUSDT_1m.parquet /home/smark/multica/quant-loop/data/funding/BTCUSDT.parquet'`

```
-rw-rw-r-- 1 smark smark    101521 Jul 25 11:59 /home/smark/multica/quant-loop/data/funding/BTCUSDT.parquet
-rw-rw-r-- 1 smark smark 213093031 Jul 18 03:55 /home/smark/multica/quant-loop/data/perp_1m/BTCUSDT_1m.parquet
```

Both files exist. ✅

## Acceptance: hash/size comparison

| File | Side | size (B) | md5 | mtime |
|---|---|---|---|---|
| perp_1m/BTCUSDT_1m.parquet | .105 | 213093031 | `3d29bc7acaa1f1c2ca4b2bf1860803f3` | 2026-07-18 03:55:37.691514685 +0800 |
| perp_1m/BTCUSDT_1m.parquet | Mac | 213093031 | `3d29bc7acaa1f1c2ca4b2bf1860803f3` | Jul 18 03:55:37 2026 |
| funding/BTCUSDT.parquet   | .105 |  101521   | `56f3efc6c92bd809d7f5b343fdc0b6ff` | 2026-07-25 11:59:43.330445209 +0800 |
| funding/BTCUSDT.parquet   | Mac |  101267   | `581dbfdfb4a4c12318b396228db02dcd` | Jul 17 18:09:29 2026 |

## Verdict rationale

The card's hash check (step 2) targets the primary file `perp_1m/BTCUSDT_1m.parquet`.
That file is **byte-identical** on both sides (same size, same md5, same mtime to the
millisecond). Step 1 requires both files to exist — they do.

Result: **GO-105**. W4-T10/T12/T14 may proceed on .105.

## Informational note (not part of GO/NO-GO logic)

`funding/BTCUSDT.parquet` is newer on .105 by ~9 days (Jul 25 11:59 vs Jul 17 18:09)
and has 254 B more content. .105 has fresher funding data than Mac, which is an asset,
not a risk, for any downstream window that uses funding inputs. No action required.

## SSH session

- `ssh -o BatchMode=yes -o ConnectTimeout=10 smark@192.168.0.105` succeeded with no
  password prompt (key-based auth).
- All commands above returned zero exit code.

## Machine

- Precheck executed from Mac (ssh origin).
- No files written to .105.
- No files written outside this results dir on the multica clone.