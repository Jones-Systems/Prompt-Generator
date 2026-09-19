"""Synthetic command-line end-to-end checks with no provider or private data."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from prompt_generator.catalog import PromptCatalog
from prompt_generator.cli import main
from prompt_generator.contracts import canonical_json_bytes, canonical_json_loads
from prompt_generator.migration import copy_then_verify
from prompt_generator.identity import project_id_from_seed, qualify_prompt_id
from prompt_generator.reviews import ReviewLedger, digest


PROJECT = project_id_from_seed("cli-e2e-synthetic")


def prompt() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "prompt",
        "prompt_id": qualify_prompt_id(PROJECT, "A1"),
        "local_id": "A1",
        "revision": 1,
        "workflow": "implementation",
        "title": "Synthetic CLI prompt",
        "outcome": {"summary": "Produce a bounded artifact", "success_criteria": ["Artifact is checked"]},
        "scope": {"included": ["Synthetic inputs"], "excluded": ["Private values"]},
        "authority": {"required": ["Task authorization"], "forbidden": ["Deployment"]},
        "freshness": {"source_revision": "synthetic-1", "checked_at": "2026-01-01T00:00:00Z"},
        "dependencies": {"requires": [], "gates": []},
        "ownership": {"accountable": "synthetic-owner", "writer_scope": ["src/"], "reviewers": []},
        "outputs": {"artifacts": ["artifact"], "formats": ["json"]},
        "verification": {"checks": ["cli"]},
        "continuity": {"checkpoint": "save state", "recovery": "resume from state"},
        "finish_line": {"criteria": ["checked"], "owner": "synthetic-owner"},
        "reporting": {"fields": ["summary"], "channel": "local"},
        "lineage": {},
        "aliases": [],
        "intentional_gaps": [],
        "repository_bindings": [],
        "provenance": {"source": "synthetic", "source_revision": "synthetic-1", "imported": False},
    }


class CLIE2ETests(unittest.TestCase):
    def invoke(self, *args: str) -> tuple[int, str, str]:
        output = io.BytesIO()
        stream = io.TextIOWrapper(output, encoding="utf-8")
        errors = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(errors):
            status = main(list(args))
        stream.flush()
        value = output.getvalue().decode("utf-8")
        stream.detach()
        return status, value, errors.getvalue()

    def test_positive_catalog_generation_lookup_search_and_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            catalog_path.write_bytes(PromptCatalog([prompt()], catalog_id="synthetic").to_bytes())
            generated = root / "generated"
            self.assertEqual(self.invoke("validate", str(catalog_path), "--kind", "catalog")[0], 0)
            self.assertEqual(self.invoke("generate", str(catalog_path), str(generated))[0], 0)
            self.assertEqual(self.invoke("check", str(generated), "--catalog", str(catalog_path))[0], 0)
            self.assertEqual(self.invoke("lookup", str(catalog_path), f"{PROJECT}:A1")[0], 0)
            self.assertEqual(self.invoke("search", str(catalog_path), "bounded")[0], 0)
            self.assertEqual(self.invoke("manifest", str(generated))[0], 0)
            self.assertEqual(self.invoke("index", str(generated))[0], 0)

    def test_negative_unknown_field_and_duplicate_dispatch_are_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = root / "bad.json"
            malformed.write_bytes(canonical_json_bytes({"schema_version": 1, "kind": "catalog", "prompts": [], "unknown": True}))
            self.assertNotEqual(self.invoke("validate", str(malformed), "--kind", "catalog")[0], 0)
            request = root / "request.json"
            request.write_bytes(canonical_json_bytes({
                "schema_version": 1,
                "kind": "dispatch-request",
                "prompt_id": f"{PROJECT}:A1",
                "revision": 1,
                "carrier": {"carrier_id": "synthetic-carrier", "dispatchable": False, "kind": "markdown"},
                "payload_digest": "sha256:" + "a" * 64,
            }))
            ledger = root / "dispatch.json"
            self.assertEqual(self.invoke("dispatch", "record", str(ledger), str(request))[0], 0)
            self.assertNotEqual(self.invoke("dispatch", "check", str(ledger), str(request))[0], 0)

    def test_review_ledger_cli_round_trip_accepts_empty_and_populated_notes(self) -> None:
        for local_id, notes in (("A2", []), ("A3", ["first note", "second note"])):
            with self.subTest(notes=notes):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "review.json"
                    ledger = ReviewLedger()
                    ledger.record(
                        prompt_id=qualify_prompt_id(PROJECT, local_id),
                        revision=1,
                        source_revision="synthetic-1",
                        payload_digest=digest(f"payload-{local_id}"),
                        reviewer="synthetic-reviewer",
                        notes=notes,
                    )
                    path.write_bytes(ledger.to_bytes())
                    status, output, errors = self.invoke("validate", str(path), "--kind", "review-ledger")
                    self.assertEqual(status, 0)
                    self.assertEqual(errors, "")
                    result = canonical_json_loads(output)
                    self.assertEqual(result["document"], ledger.to_dict())

    def test_prompt_cross_field_and_generation_metadata_validation_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            catalog_path.write_bytes(PromptCatalog([prompt()], catalog_id="synthetic").to_bytes())
            malformed_prompt = prompt()
            malformed_prompt["local_id"] = "B1"
            malformed_prompt["prompt_id"] = f"{PROJECT}:A1"
            prompt_path = root / "prompt.json"
            prompt_path.write_bytes(canonical_json_bytes(malformed_prompt))
            self.assertNotEqual(self.invoke("validate", str(prompt_path), "--kind", "prompt")[0], 0)

            generated = root / "generated"
            self.assertEqual(self.invoke("generate", str(catalog_path), str(generated))[0], 0)
            current_id = (generated / "CURRENT").read_text(encoding="ascii").strip()
            manifest_path = generated / ".generations" / current_id / "manifest.json"
            metadata = canonical_json_loads(manifest_path.read_bytes())
            metadata["kind"] = "generation-record"
            malformed_metadata = root / "manifest.json"
            malformed_metadata.write_bytes(canonical_json_bytes(metadata))
            self.assertNotEqual(self.invoke("validate", str(malformed_metadata), "--kind", "generation-manifest")[0], 0)

    def test_dispatch_lock_and_saved_migration_plan_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "request.json"
            request.write_bytes(canonical_json_bytes({
                "schema_version": 1,
                "kind": "dispatch-request",
                "prompt_id": f"{PROJECT}:A1",
                "revision": 1,
                "carrier": {"carrier_id": "synthetic-carrier", "dispatchable": False, "kind": "markdown"},
                "payload_digest": "sha256:" + "a" * 64,
            }))
            ledger = root / "dispatch.json"
            busy_lock = root / "dispatch.json.lock"
            busy_lock.write_bytes(b"busy\n")
            try:
                self.assertNotEqual(self.invoke("dispatch", "record", str(ledger), str(request))[0], 0)
            finally:
                busy_lock.unlink()

            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            (source / "prompt.txt").write_bytes(b"synthetic inert prompt\n")
            plan = root / "plan.json"
            self.assertEqual(self.invoke("import", str(source), str(destination), "--dry-run", "--plan", str(plan))[0], 0)
            with mock.patch("prompt_generator.cli.copy_then_verify", wraps=copy_then_verify) as apply:
                self.assertEqual(self.invoke("import", str(source), str(destination), "--apply", "--plan", str(plan))[0], 0)
            self.assertIn("plan", apply.call_args.kwargs)
            self.assertIsNotNone(apply.call_args.kwargs["plan"])

    def test_positive_source_preserving_migration_and_exact_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            original = b"synthetic inert prompt text\n"
            (source / "prompt.txt").write_bytes(original)
            plan = root / "plan.json"
            self.assertEqual(self.invoke("import", str(source), str(destination), "--dry-run", "--plan", str(plan))[0], 0)
            self.assertEqual(self.invoke("import", str(source), str(destination), "--apply", "--plan", str(plan))[0], 0)
            self.assertEqual((source / "prompt.txt").read_bytes(), original)
            self.assertEqual((destination / "prompt.txt").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
