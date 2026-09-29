# Prompt Generator

Prompt Generator is a Python 3.11+ library and command-line tool for building
deterministic, public-safe prompt catalogs. Canonical input is strict versioned
JSON. Generated Markdown, manifests, indexes, and migration reports are inert
data; nothing in a prompt is evaluated as Python, a shell command, a template,
or a provider request.

## Install and command surface

```text
uv run prompt-generator validate catalog.json --kind catalog
uv run prompt-generator generate catalog.json ./generated
uv run prompt-generator check ./generated --catalog catalog.json
uv run prompt-generator lookup catalog.json prj_<32-lowercase-hex>:A1
uv run prompt-generator search catalog.json implementation --workflow implementation
uv run prompt-generator manifest ./generated
uv run prompt-generator index ./generated
uv run prompt-generator dispatch check dispatch.json request.json
uv run prompt-generator dispatch record dispatch.json request.json
uv run prompt-generator import ./source ./destination --dry-run --plan plan.json
uv run prompt-generator import ./source ./destination --apply --plan plan.json
uv run prompt-generator recovery ./generated --dry-run
```

All successful commands emit one canonical JSON document with a final LF.
`check`, an invalid migration plan, and a denied duplicate dispatch return a
non-zero status. `migrate` is an explicit alias for `import`.

The library is available through `prompt_generator` and
`python -m prompt_generator`. The package exports catalog, contract,
generation, integrity, mode, dispatch, review, and migration primitives. The
`prompt-generator` entry point is declared in `pyproject.toml`.

## Public/private boundary

Examples and CI contain synthetic values only. Public code accepts opaque
private-reference request/result/error shapes, but it never resolves,
imports, fetches, caches, logs, or publishes private values. Carriers,
generated prompts, manifests, indexes, and mode examples are explicitly
`dispatchable=false`; dispatch commands only check or append a local intent
ledger and never call an application or transport.

Level 3 is stricter than a checked-in carrier: a validated ProjectRequestV2
may be rendered only with explicit `ephemeral=True` caller authorization. The
renderer does not mint or persist workspace handles. Level 2 and Level 1
remain nondispatchable synthetic contracts.

## Determinism and safety

Canonical JSON rejects duplicate keys, BOMs, non-finite or fractional numbers,
unknown fields, and unsupported versions. Generation separates payload and
whole-file digests, writes a complete immutable snapshot, and atomically
replaces `CURRENT` only after validation. Existing valid output is retained
when generation fails. Recovery is report-only and never deletes remnants.

Migration is an inert, source-preserving copy workflow. `import` and `migrate`
first produce a canonical exact plan with source digests. `--apply` re-computes
that plan, optionally compares it byte-for-byte with `--plan`, copies only
non-colliding files, verifies destination digests, and leaves the source
untouched. A plan is not provenance, authorization, or a signature.

Threats covered include path traversal, unsafe normalization, symlink and
hard-link targets, malformed markers/fences, stale snapshots, duplicate or
lineage dispatch, digest drift, migration collisions, and accidental private
dependency. SHA-256 values establish byte equality only; they do not prove
authorship, authenticity, authorization, or safe prompt instructions. Live
provider dispatch, private resolution, authentication, hosted service,
deployment, release, and merge are outside this package.

## Development

The public workflow is local to `ubuntu-latest`, uses pinned public actions,
`uv 0.12.5`, read-only repository permissions, synthetic fixtures, serial
tests, and a serial package build. It does not use shared private workflows,
secrets, or private repository fetches.
