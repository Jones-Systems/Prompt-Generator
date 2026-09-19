"""Bounded generation/integrity checks using synthetic public prompts only."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from prompt_generator.catalog import PromptCatalog
from prompt_generator.generation import (
    BEGIN_MARKER,
    END_MARKER,
    GenerationStore,
    RenderError,
    parse_prompt_markdown,
    prompt_payload_bytes,
    render_prompt,
)
from prompt_generator.contracts import canonical_json_bytes, canonical_json_loads
from prompt_generator.integrity import payload_and_file_integrity
from prompt_generator.identity import project_id_from_seed, qualify_prompt_id
from prompt_generator.paths import PathSafetyError, rooted_path, safe_relative_path


PROJECT = project_id_from_seed("generation-integrity-synthetic")


def prompt(local_id: str = "A1", revision: int = 1, **changes):
    document = {
        "schema_version": 1,
        "kind": "prompt",
        "prompt_id": qualify_prompt_id(PROJECT, local_id),
        "local_id": local_id,
        "revision": revision,
        "workflow": "implementation",
        "title": "Synthetic `` implementation",
        "outcome": {"summary": "Produce a bounded artifact", "success_criteria": ["Artifact is checked"]},
        "scope": {"included": ["Synthetic inputs"], "excluded": ["Live private values"]},
        "authority": {"required": ["Task authorization"], "forbidden": ["Deployment"]},
        "freshness": {"source_revision": "synthetic-1", "checked_at": "2026-01-01T00:00:00Z"},
        "dependencies": {"requires": [], "gates": []},
        "ownership": {"accountable": "synthetic-owner", "writer_scope": ["src/"]},
        "outputs": {"artifacts": ["artifact"], "formats": ["markdown"]},
        "verification": {"checks": ["generation-integrity"]},
        "continuity": {"checkpoint": "save state", "recovery": "resume from state"},
        "finish_line": {"criteria": ["checked"], "owner": "synthetic-owner"},
        "reporting": {"fields": ["summary"]},
        "lineage": {},
        "aliases": [],
        "intentional_gaps": [],
        "repository_bindings": [],
        "provenance": {"source": "synthetic", "source_revision": "synthetic-1", "imported": False},
    }
    document.update(changes)
    return document


class RenderingTests(unittest.TestCase):
    def test_render_is_canonical_and_round_trips(self):
        raw = render_prompt(prompt())
        self.assertEqual(raw.count(BEGIN_MARKER.encode()), 1)
        self.assertEqual(raw.count(END_MARKER.encode()), 1)
        self.assertEqual(parse_prompt_markdown(raw), prompt())
        self.assertIn(b"```json\n", raw)

    def test_malformed_markers_and_fences_are_rejected(self):
        raw = render_prompt(prompt())
        with self.assertRaises(RenderError):
            parse_prompt_markdown(raw.replace(b"<!-- prompt-generator:end -->", b"<!-- prompt-generator:end -->\n<!-- prompt-generator:end -->", 1))
        with self.assertRaises(RenderError):
            parse_prompt_markdown(raw.replace(b"```json\n", b"``json\n", 1))


class PathTests(unittest.TestCase):
    def test_traversal_and_ambiguous_components_are_rejected(self):
        with self.assertRaises(PathSafetyError):
            safe_relative_path("../outside")
        with self.assertRaises(PathSafetyError):
            safe_relative_path("a\\b")
        with self.assertRaises(PathSafetyError):
            safe_relative_path("e\u0301")

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            (root / "link").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(PathSafetyError):
                rooted_path(root, "link/file")


class SnapshotTests(unittest.TestCase):
    @staticmethod
    def _make_snapshot_writable(snapshot: Path) -> None:
        os.chmod(snapshot, 0o755)
        for path in snapshot.rglob("*"):
            os.chmod(path, 0o755 if path.is_dir() else 0o644)

    def test_publish_check_and_manifest_index_are_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = PromptCatalog([prompt()])
            store = GenerationStore(directory)
            result = store.publish(catalog)
            self.assertTrue(result.snapshot.is_dir())
            self.assertEqual(store.read_current().generation_id, result.generation_id)
            self.assertTrue(store.check(catalog).valid)
            self.assertFalse(store.check(catalog).stale)
            manifest = (result.snapshot / "manifest.json").read_bytes()
            index = (result.snapshot / "index.json").read_bytes()
            self.assertEqual(parse_prompt_markdown((result.snapshot / "prompts" / PROJECT / "A1" / "r1.md").read_bytes()), prompt())
            self.assertEqual(__import__("prompt_generator.contracts", fromlist=["canonical_json_loads"]).canonical_json_loads(manifest)["files"], __import__("prompt_generator.contracts", fromlist=["canonical_json_loads"]).canonical_json_loads(index)["files"])

    def test_stale_catalog_does_not_replace_prior_current(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GenerationStore(directory)
            first = store.publish(PromptCatalog([prompt()]))
            stale_catalog = PromptCatalog([prompt(title="Changed")])
            report = store.check(stale_catalog)
            self.assertTrue(report.stale)
            self.assertEqual(store.read_current().generation_id, first.generation_id)

    def test_recovery_is_report_only_and_keeps_unreachable_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GenerationStore(directory)
            store.publish(PromptCatalog([prompt()]))
            interrupted = store.generations / ".interrupted"
            interrupted.mkdir()
            (interrupted / "partial").write_bytes(b"partial\n")
            report = store.recover(dry_run=True)
            self.assertTrue(any(item.status == "interrupted" for item in report.items))
            self.assertTrue(interrupted.exists())

    def test_self_consistent_prompt_hashes_do_not_authorize_altered_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = PromptCatalog([prompt()])
            store = GenerationStore(directory)
            result = store.publish(catalog)
            self._make_snapshot_writable(result.snapshot)

            prompt_path = result.snapshot / "prompts" / PROJECT / "A1" / "r1.md"
            altered_prompt = prompt(title="Altered but valid payload")
            altered_bytes = render_prompt(altered_prompt)
            prompt_path.write_bytes(altered_bytes)
            altered_integrity = payload_and_file_integrity(prompt_payload_bytes(altered_prompt), altered_bytes)

            for metadata_name in ("manifest.json", "index.json"):
                metadata_path = result.snapshot / metadata_name
                metadata = canonical_json_loads(metadata_path.read_bytes())
                for entry in metadata["files"]:
                    if entry["path"] == str(prompt_path.relative_to(result.snapshot)):
                        entry.update(altered_integrity)
                for entry in metadata["prompts"]:
                    if entry["path"] == str(prompt_path.relative_to(result.snapshot)):
                        entry.update(altered_integrity)
                metadata_path.write_bytes(canonical_json_bytes(metadata))

            report = store.check(catalog)
            self.assertFalse(report.valid)
            self.assertIn("manifest files do not match validated catalog outputs", report.issues)

    def test_self_consistent_manifest_cannot_hide_missing_catalog_prompt_output(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = PromptCatalog([prompt()])
            store = GenerationStore(directory)
            result = store.publish(catalog)
            self._make_snapshot_writable(result.snapshot)

            prompt_path = result.snapshot / "prompts" / PROJECT / "A1" / "r1.md"
            prompt_path.unlink()
            for metadata_name in ("manifest.json", "index.json"):
                metadata_path = result.snapshot / metadata_name
                metadata = canonical_json_loads(metadata_path.read_bytes())
                metadata["files"] = [entry for entry in metadata["files"] if entry["kind"] != "prompt"]
                metadata["prompts"] = []
                metadata_path.write_bytes(canonical_json_bytes(metadata))

            report = store.check(catalog)
            self.assertFalse(report.valid)
            self.assertIn("manifest files do not match validated catalog outputs", report.issues)


if __name__ == "__main__":
    unittest.main()
