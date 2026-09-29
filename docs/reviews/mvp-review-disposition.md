# Prompt Generator MVP Review Disposition

Artifact Type: `review-disposition`

Artifact ID: `review.prompt-generator-mvp.001`

Purpose: Preserve exact-revision independent review findings and their remediation state.

Governing artifact: `spec.prompt-generator-mvp`

Finding owner: PGEN-A1 successor coordinator

Consumers: builders, re-reviewers, verifier, delivery owner

Authority effect: none

## Review Results

Correctness result identity: source `/root/pg_correctness_review`, profile `codex-v3-code-reviewer`, reviewed OID `3563c293218119daae0557ea540f1c0f97666696`. Security/privacy result identity: source `/root/pg_security_review`, profile `codex-v3-security-reviewer`, same reviewed OID. Both results were independent, read-only, and publication-blocking.

Each entry preserves the original severity and blocking effect. Evidence and recommendation are concise projections of the source result; the source result remains authoritative.

- `PG-CR-001` — P1, blocking. Evidence: single-file migration discarded an explicit target. Recommendation/cone: preserve the target and test renames in migration. Remediated by `66eba1726471ef00b942e1fcdf7d81ecf8cf7ad7`.
- `PG-CR-002` — P1, blocking. Evidence: snapshot validation did not reconstruct catalog outputs. Recommendation/cone: bind every prompt/file/manifest/index byte in generation. Remediated by `66eba1726471ef00b942e1fcdf7d81ecf8cf7ad7`.
- `PG-CR-003` — P1, blocking. Evidence: an alternate Level 3 renderer bypassed the ephemeral gate. Recommendation/cone: gate every Level 3 renderer in modes. Remediated by `66eba1726471ef00b942e1fcdf7d81ecf8cf7ad7`.
- `PG-CR-004` — P1, blocking. Evidence: external and runtime prompt contracts diverged. Recommendation/cone: establish schema/runtime parity and cross-field validation in contracts/catalog/CLI. Remediated by `66eba1726471ef00b942e1fcdf7d81ecf8cf7ad7`.
- `PG-CR-005` — P2, blocking. Evidence: malformed generation metadata validated successfully. Recommendation/cone: strictly validate or remove those CLI kinds. Remediated by `66eba1726471ef00b942e1fcdf7d81ecf8cf7ad7`.
- `PG-CR-006` — P2, blocking. Evidence: dispatch/review deserializers accepted unsupported versions, unknown fields, and coercions. Recommendation/cone: closed exact-typed deserializers in ledgers/CLI. Remediated by `66eba1726471ef00b942e1fcdf7d81ecf8cf7ad7`.
- `PG-CR-007` — P2, blocking. Evidence: stack children could omit current-parent evidence. Recommendation/cone: require `parent_current_oid` for every child. Remediated by `66eba1726471ef00b942e1fcdf7d81ecf8cf7ad7`.
- `PG-CR-008` — P2, blocking. Evidence: installed consumers could not retrieve schemas. Recommendation/cone: package schemas, expose digests, and smoke-test the wheel. Remediated by `66eba1726471ef00b942e1fcdf7d81ecf8cf7ad7`.
- `PGEN-SEC-001` — P2, blocking. Evidence: single-file migration followed a destination symlink. Recommendation/cone: lexical no-follow validation and exact target retention in migration. Remediated by `66eba1726471ef00b942e1fcdf7d81ecf8cf7ad7`.
- `PGEN-SEC-002` — P2, blocking. Evidence: migration publication could overwrite a competing target. Recommendation/cone: atomic no-clobber publication in migration. Remediated by `66eba1726471ef00b942e1fcdf7d81ecf8cf7ad7`.
- `PGEN-SEC-003` — P2, blocking. Evidence: self-consistent altered snapshots were not catalog-bound. Recommendation/cone: regenerate expected snapshot bytes and basis. Remediated by `66eba1726471ef00b942e1fcdf7d81ecf8cf7ad7`.
- `PGEN-SEC-004` — P2, blocking. Evidence: concurrent ledger writes bypassed duplicate protection. Recommendation/cone: lock the full CLI ledger transaction and compare its preimage. Remediated by `66eba1726471ef00b942e1fcdf7d81ecf8cf7ad7`.
- `PGEN-SEC-005` — P2, blocking. Evidence: apply recomputed rather than consumed the accepted plan. Recommendation/cone: pass and verify the exact plan. Remediated by `66eba1726471ef00b942e1fcdf7d81ecf8cf7ad7`.

Re-review at `d335ce3865d27840683e6ea51dcbc756eb846143` closed all original findings. Correctness source `/root/pg_correctness_review` then reported `PG-CR-009` (P1, blocking: empty review notes broke emitted-ledger round trips; review/CLI cone) and `PG-CR-010` (P2, blocking: duplicate gate IDs could suppress failure; coordination cone). Security source `/root/pg_security_review` reported no remaining P0–P2 security/privacy finding. `PG-CR-009` and `PG-CR-010` are remediated by `5d9a2c4fe48b2fda68b06930479ee0b6c740a9f7`; exact-revision confirmation is pending.

The security review's test-evidence caveat was reconciled directly: the forbidden `Personal-Info` literal is absent from the reviewed `docs/security.md`. Final exact re-review identity is recorded in the owning draft PR because a Git commit cannot embed its own OID.
