# Artifact Provenance and Signature Design

**Status:** design only  
**Date:** 2026-07-20  
**Recommendation:** per-agent Ed25519 signatures in artifact record metadata

## 1. Requirements and trust boundary

Each published artifact must let a consumer determine:

1. the stable agent identity that produced it;
2. the exact validator source versions and each validator result;
3. the source repository commit used to generate it; and
4. whether the artifact bytes or provenance claims changed after signing.

The signature authenticates an agent's attestation. It does not independently prove that a validator executed correctly; that assurance depends on the publisher signing only after the declared validator results are produced. A future validator co-signature would strengthen that property but is outside this minimal design.

A claimed `agent_id`, validator hash, or commit hash is useful only when it is inside the authenticated signing scope. None should be accepted from unsigned fields.

## 2. Current publishing flow

The current publisher behaves as follows:

- `flatten_metrics()` builds a flat JSON object containing strategy metrics.
- The metrics range validator runs against the raw strategy metrics. Invalid metrics are not published.
- Framework gate fields are added to the flat object: `framework_validated`, `framework_sharpe`, `framework_return_pct`, `divergence_flag`, and `kill_reason`.
- The framework cross-validation validator runs when a framework result file exists. A rejected framework result is still published with its rejection flag.
- The flat object is serialized to a temporary `*_metrics.json` file.
- The artifact CLI uploads that file with kind `metrics`. Record metadata currently contains only `campaign` and `iteration`.
- Equity CSV files are uploaded separately with kind `equity` and metadata containing `campaign`, `iteration`, and `symbol`.

The issue-map JSON contains issue ownership fields such as `assignee` and `assignee_id`, but no artifact signature, provenance, fingerprint, validator-version, commit, or approval fields. Issue assignment is therefore not usable as artifact authorship evidence.

Neither validator source defines an explicit semantic version constant. The exact source-file SHA-256 is the least ambiguous version identifier. At design time, the exact validator copies imported by the publisher match their canonical sources:

- `metrics_validator`: `sha256:0fbeb6c09521b92666319729df11a08541b209c9719a19b0af15e15490775e8f`
- `framework_cv_validator`: `sha256:2c5733bb3ad8d3c4d2df80d949d542a68146bf25aa0a2553345445cbffe35592`

The repository was dirty when inspected, and the framework validator was untracked. This demonstrates that a bare `git_commit` is insufficient unless signing requires a clean tree.

## 3. Common provenance envelope

Keep the strategy metrics JSON bytes unchanged. Put provenance in the artifact record metadata already supplied at upload time. This avoids changing the flat metrics schema and lets signatures cover JSON and non-JSON artifacts identically.

The following is the common envelope. Options A, B, and C differ only in `algorithm`, `key_id`, and `signature`.

```json
{
  "campaign": "vpvr-reversion",
  "iteration": "strategy_directory_name",
  "provenance": {
    "schema": "artifact-provenance/v1",
    "subject": {
      "task_id": "<artifact task UUID>",
      "kind": "metrics",
      "name": "strategy_directory_name_metrics.json",
      "campaign": "vpvr-reversion",
      "iteration": "strategy_directory_name"
    },
    "agent_id": "<stable agent UUID>",
    "validators": {
      "metrics_validator": {
        "source_sha256": "sha256:<64 lowercase hex characters>",
        "result": "approved"
      },
      "framework_cv_validator": {
        "source_sha256": "sha256:<64 lowercase hex characters>",
        "result": "approved|not_run|rejected"
      }
    },
    "git_commit": "<40 lowercase hex characters>",
    "git_dirty": false,
    "signed_at": "<RFC 3339 UTC timestamp>",
    "artifact_sha256": "sha256:<SHA-256 of exact uploaded file bytes>",
    "algorithm": "ed25519",
    "key_id": "agent/<agent UUID>/<rotation ID>",
    "signature": "<base64url without padding>"
  }
}
```

For an equity artifact, `subject.kind`, `subject.name`, and the outer existing metadata change as appropriate; add `subject.symbol` and require it to match the outer `symbol`. The provenance rules stay the same.

### Required field semantics

- `agent_id` is the immutable agent UUID, not a display name. It must come from authenticated runtime identity, not an arbitrary strategy file field.
- `validators.*.source_sha256` hashes the exact imported validator bytes that ran. Hashing only the canonical source is insufficient if the publisher uses a copied file.
- `validators.*.result` is mandatory. A present hash does not imply approval. If validators are unavailable, a strong provenance signature must not be issued.
- `git_commit` is captured at generation time. The publisher must verify it is still operating from that commit.
- `git_dirty` must be `false` for original provenance. The signer refuses strong signing for dirty or untracked source trees because the commit would not fully identify the generating code.
- `artifact_sha256` hashes exact upload bytes, after serialization and before upload.
- `subject` binds the signature to the artifact record context and prevents an otherwise valid artifact from being silently relabeled or attached to another task. Its `campaign`, `iteration`, and optional `symbol` must match the corresponding outer record metadata.
- `signed_at` records signer clock time; it is not a trusted timestamp authority.

### Canonical signing input

To avoid canonicalizing metric floats, sign record metadata plus a digest of the exact file bytes, not a reserialized metrics object.

1. Copy `provenance` and remove only `signature`.
2. Serialize that unsigned object as UTF-8 JSON using sorted object keys, no insignificant whitespace, UTF-8 characters unescaped, and rejection of NaN/Infinity. In Python terms, the contract is `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`, and `allow_nan=False`.
3. The signing message is the UTF-8 bytes of the domain separator `artifact-provenance/v1`, followed by one NUL byte, followed by the canonical unsigned-provenance bytes.

The unsigned provenance contains only strings, booleans, and objects, so cross-language verification does not depend on floating-point formatting. `artifact_sha256` commits the signature to the exact payload bytes.

A verifier must reject unknown schemas or algorithms rather than guessing a fallback.

## 4. Option A — SHA-256 fingerprint only

### Format

Use the common envelope with:

```json
{
  "algorithm": "sha256",
  "key_id": null,
  "signature": null,
  "artifact_sha256": "sha256:<64 lowercase hex characters>"
}
```

The agent, validator, commit, timestamp, and subject fields may still be recorded, but they are unauthenticated claims.

### Signing mechanism

There is no signature. Compute SHA-256 over the exact uploaded file bytes and store the digest in `artifact_sha256`.

### Verification

The consumer downloads the artifact, recomputes SHA-256 over the exact bytes, and compares it with `artifact_sha256`.

### Pros

- Uses only the Python standard library.
- No keys, key registry, rotation, or revocation.
- Negligible CPU cost.
- About 64 hexadecimal characters plus the common metadata.
- Works for JSON, CSV, and any future artifact type.

### Cons

- Does not prove authorship.
- Does not authenticate validator or commit claims.
- A malicious party able to change both artifact and metadata can recompute the digest. It detects accidental corruption, not hostile tampering, unless the digest is separately anchored in an immutable trusted store.
- Therefore it does not fully satisfy the stated requirements.

### Backwards compatibility

Existing artifacts can be fingerprinted without changing their bytes. Historical author, validator, and generation commit cannot be established by doing so. Such records must be labeled as retroactive inventory, not original provenance.

## 5. Option B — per-agent HMAC-SHA-256

A single workspace-wide HMAC key cannot cryptographically distinguish agents: any holder can sign while claiming any `agent_id`. To meet the authorship requirement as closely as symmetric cryptography allows, this option uses a unique HMAC key per agent.

### Format

Use the common envelope with:

```json
{
  "algorithm": "hmac-sha256",
  "key_id": "agent/<agent UUID>/<rotation ID>",
  "signature": "<43-character base64url HMAC without padding>"
}
```

### Signing mechanism

Compute HMAC-SHA-256 over the canonical signing input with the secret selected by `key_id`. Each agent receives only its own key. The signed `agent_id` must match the key registry entry for `key_id`.

A root workspace secret may derive per-agent keys centrally, but agents must receive only the derived key, never the root from which other agents' keys can be derived.

### Verification

1. Recompute and compare `artifact_sha256` using constant-time comparison.
2. Confirm the API record's task, kind, name, campaign, iteration, and optional symbol match `subject`.
3. Resolve `key_id` in the trusted key registry and require its registered agent to equal `agent_id`.
4. Recreate the canonical signing input.
5. Recompute HMAC-SHA-256 and compare the decoded MAC in constant time.
6. Check key validity/revocation at `signed_at`, validator allow-list status, `git_dirty == false`, and the expected source commit if the repository is available.

### Pros

- Fully supported by Python standard-library `hashlib` and `hmac`.
- Very small 32-byte MAC, encoded as 43 base64url characters.
- Negligible signing and verification cost.
- Strong integrity and per-agent attribution against parties that do not possess that agent's secret.
- Less operational setup than asymmetric keys.

### Cons

- Every verifier needs the secret or access to a trusted verification service.
- Any verifier holding the secret can forge artifacts for that agent; this conflicts with "any consumer can verify" when consumers are not all trusted signers.
- Per-agent secret distribution, rotation, leakage response, and revocation are required.
- A workspace-wide key is simpler but proves only workspace origin, not which agent wrote the artifact.

### Backwards compatibility

Existing artifact bytes can be signed now, but the new MAC proves only that the current key holder made a retroactive attestation. It cannot recover original authorship or validator execution. Full original provenance requires regeneration and republication from a clean recorded commit. If retro-attestation is operationally useful, it must identify the migration signer and be explicitly marked `attestation: "retroactive"`.

## 6. Option C — per-agent Ed25519 signature (recommended)

This replaces the larger RSA candidate with Ed25519. The host already provides OpenSSL 3 with Ed25519 support, so no third-party Python crypto package is required. If an external policy mandates RSA, RSA-PSS-SHA-256 can use the same envelope and verification flow, at higher key and signature cost.

### Format

Use the common envelope exactly as shown:

```json
{
  "algorithm": "ed25519",
  "key_id": "agent/<agent UUID>/<rotation ID>",
  "signature": "<86-character base64url Ed25519 signature without padding>"
}
```

Maintain a trusted public-key registry entry for each key:

```json
{
  "key_id": "agent/<agent UUID>/<rotation ID>",
  "agent_id": "<stable agent UUID>",
  "algorithm": "ed25519",
  "public_key": "<base64url public key>",
  "valid_from": "<RFC 3339 timestamp>",
  "revoked_at": null
}
```

### Signing mechanism

The agent signs the canonical signing input with its Ed25519 private key. The private key remains readable only by that agent runtime. The public key and its binding to `agent_id` are published in the trusted registry.

### Verification

1. Recompute and compare `artifact_sha256`.
2. Confirm the API record's task, kind, name, campaign, iteration, and optional symbol match `subject`.
3. Resolve `key_id` to a trusted public key and require the registered `agent_id` to equal the signed `agent_id`.
4. Recreate the canonical signing input and verify the Ed25519 signature.
5. Check key validity/revocation at `signed_at`.
6. Require `git_dirty == false`; optionally fetch or inspect `git_commit`.
7. Compare validator source hashes with known/allowed versions and interpret each explicit validator result. A valid signature over `result: "rejected"` is valid provenance, not validator approval.

### Pros

- Satisfies public verification: consumers need only public keys and cannot forge signatures.
- Cryptographically distinguishes agents through per-agent keypairs.
- Strong integrity for artifact bytes and every signed provenance field.
- Compact 64-byte signature, encoded as 86 base64url characters; 32-byte public key stored once per rotation.
- Fast signing and verification.
- Available through the existing OpenSSL 3 installation; no added Python dependency.
- Cleaner trust boundary than HMAC because verifiers are not signers.

### Cons

- Requires private-key provisioning, file permissions, rotation, revocation, backup policy, and a trusted public-key registry.
- Agent environments must invoke the existing OpenSSL tooling or a future approved crypto binding.
- More operational work than a digest or HMAC.
- As with every option, a compromised agent private key can sign false validator or commit claims until revoked.

### Backwards compatibility

The artifact payload format remains unchanged because provenance is record metadata. Existing consumers that ignore unknown metadata continue to work. Old bytes can receive a retroactive signature, but that signature proves only the current signer's attestation at the new timestamp. To claim original writer, validator approval, and generating commit, regenerate and republish the artifact under the new scheme.

## 7. Comparison

| Property | A: SHA-256 | B: per-agent HMAC | C: per-agent Ed25519 |
|---|---:|---:|---:|
| Artifact tamper evidence | Accidental only if digest is co-located | Strong against non-key-holders | Strong against non-private-key-holders |
| Authenticates agent | No | Yes within a trusted symmetric domain | Yes via public-key binding |
| Any consumer can verify without signing power | No | No | Yes |
| Signature/MAC bytes | 0 beyond 32-byte digest | 32 | 64 |
| Key management | None | Per-agent shared secrets | Per-agent private keys + public registry |
| Agent-side cost | Negligible | Negligible | Very low |
| New Python crypto dependency | No | No | No; existing OpenSSL 3 is sufficient |
| Meets all stated requirements | No | Only if every verifier is trusted with secrets | Yes |

## 8. Recommendation and minimal rollout policy

Choose **Option C: per-agent Ed25519**. It is the simplest option that meets all four requirements without giving every verifier the power to forge artifacts. Its storage and CPU costs are trivial, and the required asymmetric capability already exists on the host.

The minimum policy needed for the signature to mean what it claims is:

1. Use one private key per stable agent identity and a trusted `key_id` to public-key/agent registry.
2. Capture generation commit at generation time and refuse original-provenance signing unless the source tree is clean and still at that commit.
3. Hash the exact validator files imported by the publisher and record explicit results; never treat a hash alone as approval.
4. Refuse strong signing if validator imports are unavailable.
5. Hash the final exact artifact bytes, bind task/kind/name/campaign/iteration and optional symbol, and then sign the canonical provenance.
6. Apply the same envelope to metrics JSON and equity CSV artifacts.
7. Keep old artifacts readable. Do not present retroactive signatures as original provenance; regenerate and republish when original provenance is required.

If deployment must be staged, Option B is acceptable only as an internal transitional scheme where verification occurs in a trusted service. Option A is useful as an inventory checksum but should not be described as a digital signature.

## 9. Environment check

The host check succeeded for Python standard-library `hashlib` and `hmac`. OpenSSL 3.0.13 is installed and reports Ed25519 support. The recommended scheme therefore introduces no new crypto package dependency.
