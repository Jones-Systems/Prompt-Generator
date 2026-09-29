# Engineering Spec — Prompt Generator MVP

<!-- codex-section:begin id="spec.prompt-generator-mvp#ctx.artifact-header.001" -->
Artifact Type: `engineering-spec`

Artifact ID: `spec.prompt-generator-mvp`

Purpose: Define the implementation-ready public Prompt Generator MVP.

Governing artifact: `none`

Specification owner: PGEN-A1 successor coordinator

Integration owner: PGEN-A1 successor coordinator

Consumers: builders, compiler, reviewers, verifier, downstream Personal Info contract consumer

Authority effect: none
<!-- codex-section:end id="spec.prompt-generator-mvp#ctx.artifact-header.001" -->

<!-- codex-section:begin id="spec.prompt-generator-mvp#ctx.project-summary.001" -->
## Project Summary

Build a portable Python 3.13 CLI/library whose canonical source is strict versioned JSON and whose generated Markdown, indexes, manifests, carriers, review records, dispatch checks, and migration reports are deterministic. The public repository owns opaque private-reference request/result/error shapes but never values or resolver access. Primary modes are distinct `level-2.v1` and `level-3.v1`; `level-1.v1` is an explicit reduced fallback. The finish line is a verified, independently reviewed, privacy-cleared draft pull request. Hosted service, live dispatch, live private resolution, release, deployment, and merge are excluded.
<!-- codex-section:end id="spec.prompt-generator-mvp#ctx.project-summary.001" -->

<!-- codex-section:begin id="spec.prompt-generator-mvp#req.mvp.001" -->
## Requirements

- Use Draft 2020-12 schemas, strict duplicate-key and unsupported-version rejection, Python `argparse`, and a constrained canonical JSON v1 profile: sorted keys, compact separators, UTF-8, integers only, no BOM/nonfinite numbers, exactly one LF.
- Canonical workflows are `research`, `planning`, `implementation`, `verification`, `review`, `integration`, `conflict-resolution`, `correction`, `resume`, `migration`, and `handoff`; documented aliases never create identities.
- A project ID is `prj_` plus 32 lowercase hex characters and is independent of repository bindings. Qualified prompt identity is `<project-id>:<grouped-local-id>`; revision, mode, wave, branch, PR, and attempt remain separate.
- Catalogs model outcomes, scope/exclusions, authority/freshness, dependencies/gates, ownership/writer scope, outputs, verification, continuity/recovery, finish line, reporting, lineage, aliases, intentional gaps, repository bindings, and provenance.
- Generate canonical one-prompt documents with paired live markers outside an exact copy fence. Carriers and indexes are unmarked and `dispatchable=false`.
- Track payload digest/bytes separately from whole-file digest/bytes; hashes establish byte equality only. Review evidence binds exact source revision and payload digest and is invalidated by material changes.
- Publish complete immutable snapshots below `.generations/<digest>/` and atomically replace a regular `CURRENT` pointer only after full validation. Preserve the prior valid snapshot on failure and classify interruption remnants without automatic deletion.
- Reject traversal, unsafe or ambiguously normalized IDs/slugs, reserved filenames, symlink escapes, hardlinked writable targets, malformed markers/fences, stale outputs, collisions, unsupported versions, and unknown effects.
- Provide validate, generate/check, lookup/search, manifest/index, dispatch-check/record, dry-run import/migration, and recovery commands. Imports are inert data; apply is copy-then-verify and preserves sources.
- Mode validation fails closed on missing bindings and checks structured fields rather than naive token bans. Public examples are synthetic and nondispatchable. Live Level 3 rendering is caller-authorized and ephemeral only.
- Public CI and runtime never fetch, import, resolve, cache, log, or publish `Personal-Info` values.
<!-- codex-section:end id="spec.prompt-generator-mvp#req.mvp.001" -->

<!-- codex-section:begin id="spec.prompt-generator-mvp#ac.mvp.001" -->
## Acceptance

The installed CLI/library passes unit and end-to-end tests for identity/schema round trips, deterministic bytes, integrity and atomic recovery, catalog/dispatch/review guards, all three modes, migration, coordination/stack contracts, and public/private isolation. A second generation is byte-identical; failed publication leaves the old `CURRENT` readable. Synthetic positive and negative Level 2/3 fixtures exercise missing/stale/cross-mode bindings without live apps or handles. Public history and candidate content pass bounded mechanical and semantic privacy review, and exact-revision correctness plus security/privacy reviews have no unresolved blocking findings.
<!-- codex-section:end id="spec.prompt-generator-mvp#ac.mvp.001" -->

<!-- codex-section:begin id="spec.prompt-generator-mvp#ctx.research-decision-basis.001" -->
## Research And Decision Basis

Current Codex-V3 authority is pinned at `4911fbff0c87166d14870658dd938ef163b40085`, including exact Level 2 and Level 3 source blobs recorded in `worknote.prompt-generator-mvp`. Company-campaign evidence is pinned at `39e6f617beee7be7d533d3ff7e85a13cbd2a0e6d`; it proves qualified hash keys, marker/fence rules, unmarked carriers, and corrected alias/EOF/fictional-coordinator cases but supplies no license grant for import. The 139-item app campaign proves sibling-first coordination and exact stack invalidation but is not public-safe for raw import. Primary Python, JSON Schema, packaging, CommonMark, and filesystem documentation support Python 3.13, Draft 2020-12, stdlib CLI/serialization, and same-filesystem atomic replacement. The architecture screen is complete for the named public CLI/library, generated snapshot reader, public/private contract consumer, CI, and migration universe at these revisions; live authentication/storage/deployment consumers remain intentionally excluded.
<!-- codex-section:end id="spec.prompt-generator-mvp#ctx.research-decision-basis.001" -->

<!-- codex-section:begin id="spec.prompt-generator-mvp#ctx.change-surface.001" -->
## Exact Change Surface And Ownership

The coordinator is sole Git/index owner. Planned file cones are packaging/instructions; `src/prompt_generator/`; `schemas/`; `templates/`; `examples/`; `tests/`; `.github/workflows/`; and documentation. Builders may edit only assigned non-overlapping files and never stage or commit. The public contract schemas are independently consumed by Personal Info and therefore receive stable versioned files and exact digests. Direct consumers are CLI users, library callers, tests, CI, and the private vendoring step; indirect generated-output consumers are bounded by manifest/schema versions.
<!-- codex-section:end id="spec.prompt-generator-mvp#ctx.change-surface.001" -->

<!-- codex-section:begin id="spec.prompt-generator-mvp#iface.contracts.001" -->
## Interface Contracts

Canonical JSON v1 rejects unknown fields and unsafe numbers. Project and prompt identities are immutable; locations and aliases resolve to them. Snapshot readers pin one validated `CURRENT` generation. Level 2 carries GitHub+Scratch authority, source/base/head/task-branch and recovery bindings but no workspace handle. Level 3 carries actual repository/root/branch/initial HEAD/base/delivery/publication transport/current workspace binding and never mints or reuses a handle. Private-resolution requests carry only version, opaque reference, purpose, and scopes; caller authentication belongs to the private invocation context. Compatibility is explicit by schema/mode/generator version; migration never mutates sources.
<!-- codex-section:end id="spec.prompt-generator-mvp#iface.contracts.001" -->

## Test Plan

<!-- codex-section:begin id="spec.prompt-generator-mvp#test.mvp.001" -->
```json
{
  "schema_version": 1,
  "kind": "integration",
  "validates": [
    "spec.prompt-generator-mvp#req.mvp.001",
    "spec.prompt-generator-mvp#ac.mvp.001",
    "spec.prompt-generator-mvp#iface.contracts.001"
  ],
  "scenario": "The synthetic public MVP exercises contracts, generation, modes, catalog guards, migration, coordination, CLI installation, and public/private isolation without live private data.",
  "evidence": {
    "predicate": "the affected-check union and package smoke pass on the exact candidate",
    "invalid": "tests use live handles, private values, or bypass production library paths",
    "unknown": "the candidate revision, environment, or generated snapshot is not exact"
  },
  "environment": [
    "Python 3.13 with synthetic fixtures only"
  ],
  "obligation_owner_task": "spec.prompt-generator-mvp#task.integration.001",
  "evidence_executor_task": "spec.prompt-generator-mvp#task.integration.001",
  "acceptance_owner": "pgen-a1-coordinator",
  "activation": {
    "after_accepted": [
      "spec.prompt-generator-mvp#task.foundation.001",
      "spec.prompt-generator-mvp#task.generation.001",
      "spec.prompt-generator-mvp#task.modes.001",
      "spec.prompt-generator-mvp#task.catalog-migration.001"
    ]
  },
  "target_hints": [
    "tests"
  ],
  "affected_check_groups": [
    "contracts-identity",
    "generation-integrity",
    "modes",
    "catalog-dispatch-review",
    "migration",
    "coordination-stack",
    "package-cli",
    "publication-safety"
  ],
  "required_at": "candidate-review-and-verification"
}
```
<!-- codex-section:end id="spec.prompt-generator-mvp#test.mvp.001" -->

## Parallelization And Task Plan

<!-- codex-section:begin id="spec.prompt-generator-mvp#plan.parallelization.001" -->
```json
{
  "schema_version": 1,
  "default_context_refs": [
    "spec.prompt-generator-mvp#ctx.project-summary.001",
    "spec.prompt-generator-mvp#iface.contracts.001"
  ],
  "deferred_findings_sink_ref": "worknote.prompt-generator-mvp#ctx.blocker-decision.001",
  "artifact_path_hints": {
    "spec.prompt-generator-mvp": "docs/engineering-specs/prompt-generator-mvp.md",
    "worknote.prompt-generator-mvp": "docs/work-notes/prompt-generator-mvp.md"
  },
  "launch_policy": "start_all_currently_launchable_with_rolling_capacity_refill",
  "machine_block_source_rendering": "deterministic_two_space_json",
  "runtime_rendering": "canonical_compact_json",
  "fan_in_owner": "pgen-a1-coordinator",
  "additional_final_check_groups": [
    "publication-safety"
  ],
  "delivery_boundary": "exact verified and independently reviewed task head published to one draft pull request without merge, release, or deployment"
}
```
<!-- codex-section:end id="spec.prompt-generator-mvp#plan.parallelization.001" -->

<!-- codex-section:begin id="spec.prompt-generator-mvp#task.foundation.001" -->
```json
{
  "schema_version": 1,
  "kind": "implementation",
  "profile_role": "builder",
  "accountable_owner": "pgen-a1-coordinator",
  "objective": "Implement packaging, strict public schemas, identity, and catalog foundations.",
  "context_refs": ["spec.prompt-generator-mvp#req.mvp.001", "spec.prompt-generator-mvp#iface.contracts.001"],
  "read_scope": ["AGENTS.md", "docs/engineering-specs/prompt-generator-mvp.md"],
  "write_scope": ["pyproject.toml", "schemas", "src/prompt_generator/contracts.py", "src/prompt_generator/identity.py", "src/prompt_generator/catalog.py", "tests/test_contracts_identity.py"],
  "requires": ["spec.prompt-generator-mvp#req.mvp.001"],
  "produces": [],
  "dependencies": [],
  "dependency_inputs": [],
  "activation": {"mode": "automatic"},
  "acceptance_refs": ["spec.prompt-generator-mvp#ac.mvp.001"],
  "test_refs": ["spec.prompt-generator-mvp#test.mvp.001"],
  "affected_check_groups": ["contracts-identity"],
  "result_fields": ["summary", "changed_paths", "evidence_refs", "produced_refs", "deferred_findings"],
  "preserved_boundaries": ["public-safe synthetic data only", "no private repository dependency"],
  "recovery_boundaries": ["stop on schema or identity ambiguity"],
  "fan_in_boundaries": [],
  "stop_conditions": ["writer scope overlaps another live task", "unexpected repository state"]
}
```
<!-- codex-section:end id="spec.prompt-generator-mvp#task.foundation.001" -->

<!-- codex-section:begin id="spec.prompt-generator-mvp#task.generation.001" -->
```json
{
  "schema_version": 1,
  "kind": "implementation",
  "profile_role": "builder",
  "accountable_owner": "pgen-a1-coordinator",
  "objective": "Implement safe paths, integrity, deterministic rendering, atomic snapshots, check mode, and recovery classification.",
  "context_refs": ["spec.prompt-generator-mvp#iface.contracts.001"],
  "read_scope": ["schemas", "src/prompt_generator/contracts.py", "src/prompt_generator/catalog.py"],
  "write_scope": ["src/prompt_generator/generation.py", "src/prompt_generator/integrity.py", "src/prompt_generator/paths.py", "tests/test_generation_integrity.py"],
  "requires": ["spec.prompt-generator-mvp#req.mvp.001"],
  "produces": [],
  "dependencies": ["spec.prompt-generator-mvp#task.foundation.001"],
  "dependency_inputs": [{"task_ref": "spec.prompt-generator-mvp#task.foundation.001", "fields": ["evidence_refs"]}],
  "activation": {"mode": "automatic"},
  "acceptance_refs": ["spec.prompt-generator-mvp#ac.mvp.001"],
  "test_refs": ["spec.prompt-generator-mvp#test.mvp.001"],
  "affected_check_groups": ["generation-integrity"],
  "result_fields": ["summary", "changed_paths", "evidence_refs", "deferred_findings"],
  "preserved_boundaries": ["prior valid output survives failure", "no automatic pruning"],
  "recovery_boundaries": ["classify unreachable generations without deletion"],
  "fan_in_boundaries": [],
  "stop_conditions": ["foundation contract is unavailable", "atomicity precondition is not satisfied"]
}
```
<!-- codex-section:end id="spec.prompt-generator-mvp#task.generation.001" -->

<!-- codex-section:begin id="spec.prompt-generator-mvp#task.modes.001" -->
```json
{
  "schema_version": 1,
  "kind": "implementation",
  "profile_role": "builder",
  "accountable_owner": "pgen-a1-coordinator",
  "objective": "Implement separate Level 1, Level 2, and Level 3 contracts, templates, and synthetic fixtures.",
  "context_refs": ["spec.prompt-generator-mvp#iface.contracts.001"],
  "read_scope": ["schemas", "src/prompt_generator/contracts.py"],
  "write_scope": ["src/prompt_generator/modes.py", "templates", "examples/synthetic", "tests/test_modes.py"],
  "requires": ["spec.prompt-generator-mvp#req.mvp.001"],
  "produces": [],
  "dependencies": ["spec.prompt-generator-mvp#task.foundation.001"],
  "dependency_inputs": [{"task_ref": "spec.prompt-generator-mvp#task.foundation.001", "fields": ["evidence_refs"]}],
  "activation": {"mode": "automatic"},
  "acceptance_refs": ["spec.prompt-generator-mvp#ac.mvp.001"],
  "test_refs": ["spec.prompt-generator-mvp#test.mvp.001"],
  "affected_check_groups": ["modes"],
  "result_fields": ["summary", "changed_paths", "evidence_refs", "deferred_findings"],
  "preserved_boundaries": ["never mint live bindings", "Level 1 never silently substitutes for a primary mode"],
  "recovery_boundaries": ["reject stale or missing mode bindings"],
  "fan_in_boundaries": [],
  "stop_conditions": ["authoritative mode contract is unresolved", "fixture contains a live binding"]
}
```
<!-- codex-section:end id="spec.prompt-generator-mvp#task.modes.001" -->

<!-- codex-section:begin id="spec.prompt-generator-mvp#task.catalog-migration.001" -->
```json
{
  "schema_version": 1,
  "kind": "implementation",
  "profile_role": "builder",
  "accountable_owner": "pgen-a1-coordinator",
  "objective": "Implement dispatch and review ledgers, migration, coordination contracts, and their synthetic tests.",
  "context_refs": ["spec.prompt-generator-mvp#req.mvp.001", "spec.prompt-generator-mvp#iface.contracts.001"],
  "read_scope": ["schemas", "src/prompt_generator/catalog.py", "src/prompt_generator/identity.py"],
  "write_scope": ["src/prompt_generator/dispatch.py", "src/prompt_generator/reviews.py", "src/prompt_generator/migration.py", "src/prompt_generator/coordination.py", "examples/coordination", "tests/test_catalog_dispatch_review.py", "tests/test_migration.py", "tests/test_coordination.py"],
  "requires": ["spec.prompt-generator-mvp#req.mvp.001"],
  "produces": [],
  "dependencies": ["spec.prompt-generator-mvp#task.foundation.001"],
  "dependency_inputs": [{"task_ref": "spec.prompt-generator-mvp#task.foundation.001", "fields": ["evidence_refs"]}],
  "activation": {"mode": "automatic"},
  "acceptance_refs": ["spec.prompt-generator-mvp#ac.mvp.001"],
  "test_refs": ["spec.prompt-generator-mvp#test.mvp.001"],
  "affected_check_groups": ["catalog-dispatch-review", "migration", "coordination-stack"],
  "result_fields": ["summary", "changed_paths", "evidence_refs", "deferred_findings"],
  "preserved_boundaries": ["imported prompts remain inert data", "sources are never edited or deleted", "coordination is not a GitHub control plane"],
  "recovery_boundaries": ["dry-run precedes apply", "unknown dispatch effect requires reconciliation"],
  "fan_in_boundaries": [],
  "stop_conditions": ["foundation identity contract changes", "real-pack licensing or privacy is unresolved"]
}
```
<!-- codex-section:end id="spec.prompt-generator-mvp#task.catalog-migration.001" -->

<!-- codex-section:begin id="spec.prompt-generator-mvp#task.integration.001" -->
```json
{
  "schema_version": 1,
  "kind": "implementation",
  "profile_role": "builder",
  "accountable_owner": "pgen-a1-coordinator",
  "objective": "Integrate CLI, documentation, CI, public boundary checks, and the final affected test suite.",
  "context_refs": ["spec.prompt-generator-mvp#ctx.project-summary.001", "spec.prompt-generator-mvp#test.mvp.001"],
  "read_scope": ["src/prompt_generator", "schemas", "templates", "examples"],
  "write_scope": ["src/prompt_generator/__init__.py", "src/prompt_generator/__main__.py", "src/prompt_generator/cli.py", "README.md", "docs/security.md", "docs/migration.md", ".github/workflows/ci.yml", "tests/test_cli.py", "tests/test_publication_safety.py"],
  "requires": ["spec.prompt-generator-mvp#req.mvp.001"],
  "produces": [],
  "dependencies": ["spec.prompt-generator-mvp#task.foundation.001", "spec.prompt-generator-mvp#task.generation.001", "spec.prompt-generator-mvp#task.modes.001", "spec.prompt-generator-mvp#task.catalog-migration.001"],
  "dependency_inputs": [],
  "activation": {"mode": "automatic"},
  "acceptance_refs": ["spec.prompt-generator-mvp#ac.mvp.001"],
  "test_refs": ["spec.prompt-generator-mvp#test.mvp.001"],
  "affected_check_groups": ["package-cli", "publication-safety"],
  "result_fields": ["summary", "changed_paths", "evidence_refs", "candidate_binding", "deferred_findings"],
  "preserved_boundaries": ["public CI has no private access", "draft publication only"],
  "recovery_boundaries": ["candidate changes invalidate affected evidence"],
  "fan_in_boundaries": ["integrate only complete predecessor file cones"],
  "stop_conditions": ["predecessor result is incomplete", "privacy scan reports a suspected finding"]
}
```
<!-- codex-section:end id="spec.prompt-generator-mvp#task.integration.001" -->

<!-- codex-section:begin id="spec.prompt-generator-mvp#ctx.operator-interaction-inventory.001" -->
## Operator Interaction Inventory

| Phase or operation | Exact owner action | Frequency | Interaction class | Executing capability / native authority | Allowed and forbidden scope | Readback | Fallback / stop |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Repository bootstrap | Already authorized in the initiating request; no remaining owner mechanics | Once | none remaining | authenticated GitHub client | exact two repositories only; no visibility changes after creation | repository ID, visibility, default branch, initial head | stop on variance or unknown effect |
| Normal local use and tests | none | per use | none | rootless Python CLI | task-owned files and synthetic fixtures only | typed command result and tests | fail closed |
| Diagnostics and recovery | none | as needed | none | read-only CLI checks and `recover --dry-run` | classify exact output root; no automatic deletion | current pointer and generation report | preserve prior output and report unknown state |
| Normal branch publication and draft PR | none beyond the existing task authorization | once per coherent candidate | mechanics | authenticated Git/GitHub clients | normal non-force task-branch push and one draft PR only | remote head and exact PR base/head/state | reconcile before retry |
| Upgrade or rollback | separate reviewed task branch; owner judgment only if scope changes | infrequent | judgment when material | Git history and versioned migrations | no release, deployment, force, or history rewrite | exact schema/generator versions and checks | preserve prior compatible version |
| Credential enrollment or rotation | not part of the MVP | exceptional | authentication | credential owner | no new credentials or secret access | availability only, never secret contents | stop |
| Destructive or forced effect | not authorized | exceptional | exact new approval required | separately selected typed capability | no cleanup, merge, deletion, force push, or history rewrite | target-specific postcondition | reject without approval |
| Live private rendering or ingestion | not implemented; separately authorized future workflow | exceptional | approval, authentication, and judgment | future trusted private adapter | never public artifacts or logs | redacted typed receipt | stop |
<!-- codex-section:end id="spec.prompt-generator-mvp#ctx.operator-interaction-inventory.001" -->

<!-- codex-section:begin id="spec.prompt-generator-mvp#ctx.stop-recovery-delivery.001" -->
## Stop, Recovery, And Delivery Boundaries

Stop mutation on repository/branch/ownership drift, overlapping writers, unsupported source versions, suspected private content, real-pack license uncertainty, unknown remote effects, or lost rollback. Reconcile generations and Git/GitHub state before retry. Final evidence is the affected-check union, package smoke, exact-diff inspection, independent correctness and security/privacy review with remediation/re-review, public tree plus reachable-history scan, normal task-branch push, draft PR readback, and confirmation that no public artifact or CI path accesses private values. No merge, release, deployment, live ingestion, source deletion, or cleanup is included.
<!-- codex-section:end id="spec.prompt-generator-mvp#ctx.stop-recovery-delivery.001" -->
