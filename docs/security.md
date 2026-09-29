# Security and privacy boundary

Prompt Generator is a public, local data tool. Its security property is
fail-closed handling of untrusted JSON, Markdown, filesystem paths, and
migration inputs. It is not a sandbox or an execution engine.

## Threat model

The input may contain hostile prompt text, duplicate JSON keys, malformed
markers, traversal components, symlinks, hard links, stale revisions, forged
digests, or instructions that look like executable code. The library treats
all prompt and import content as inert bytes. Strict canonical JSON, exact
marker/fence parsing, safe path checks, separate payload/file digests, and
copy-then-verify migration prevent those inputs from becoming an implicit
execution or overwrite path.

Generation validates a complete snapshot before publishing `CURRENT` and
preserves the previous valid pointer if publication fails. Recovery classifies
invalid or interrupted entries without deleting them. Dispatch and review
ledgers are local evidence and duplicate guards, not transport controls or
approval systems.

## Public/private boundary

The repository may carry only synthetic examples and opaque private-reference
contract shapes. It must not contain private values, handles, mappings, or
resolved references. The runtime has no private connector, network client,
credential reader, resolver, cache, or private repository dependency. CI uses
only public checkout and public package sources.

Every checked-in carrier and mode example is `dispatchable=false`. A dispatch
check or record can therefore describe a local intent without executing it.
No command in this package sends a prompt, opens an app, invokes a connector,
or follows a link from prompt data.

## Level 3 and limits

Level 3 validation can inspect a synthetic exact ProjectRequestV2 shape. A
caller must explicitly request `render_project_request(..., ephemeral=True)`
to obtain its canonical bytes. This is a caller-authorized ephemeral boundary:
the package does not mint, reuse, persist, or resolve a workspace handle.
Level 3 rendering is not a live dispatch capability.

SHA-256 digests provide exact byte comparison only. They are not signatures,
provenance, authorization, or evidence that prompt instructions are safe.
Filesystem checks reduce accidental escapes but do not replace operating-system
permissions or a hostile-host boundary. Hosted service, authentication, live
private resolution, dispatch, deployment, release, merge, and destructive
cleanup require a separate trusted workflow and authority.
