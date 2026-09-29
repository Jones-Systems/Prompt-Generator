# Source-preserving migration

`prompt-generator import` and its `migrate` alias move inert source bytes into
a destination after a deterministic dry-run. The source is never edited or
deleted, and imported prompt text is never executed or interpreted as a
template.

## Plan and apply

```text
prompt-generator import ./source ./destination --dry-run --plan migration.json
prompt-generator import ./source ./destination --apply --plan migration.json
```

The dry-run report includes every source and target path, byte count, SHA-256
source digest, detected prompt identity/revision when present, collision/error
status, and optional provenance. The report itself is canonical JSON. Applying
recomputes the plan and, when `--plan` is supplied, requires byte-for-byte
equality with the saved plan before writing. Each new target is written through
a same-directory temporary, verified by digest, and then atomically replaced.
An existing different target is a collision and is never overwritten; an
existing equal target is idempotently unchanged.

The plan digest identifies the exact report bytes. It is an integrity aid, not
a signature or authorization. Provenance fields record caller-supplied source
metadata and do not grant a license. Real campaign imports remain outside the
public MVP until ownership, licensing, privacy, and provenance are separately
settled.

## Failure and recovery

Traversal, symlink components, hard-linked targets, malformed source JSON,
duplicate prompt identities, and target changes after planning fail closed.
Sources and already-valid destination files remain available for inspection.
The migration command has no cleanup mode. Generation remnants are inspected
separately with `prompt-generator recovery ROOT --dry-run`, which only reports
classification and never deletes unreachable entries.
