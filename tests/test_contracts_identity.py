import unittest
import hashlib
import json
from pathlib import Path
import re

from prompt_generator.catalog import CatalogError, PromptCatalog, validate_catalog
from prompt_generator.contracts import (
    CanonicalJSONError,
    DuplicateKeyError,
    PrivateReferenceError,
    PrivateReferenceRequest,
    PrivateReferenceResult,
    UnsupportedVersionError,
    canonical_json_bytes,
    canonical_json_loads,
    CANONICAL_WORKFLOWS,
    SCHEMA_RESOURCE_FILES,
    schema_bytes,
    schema_digest,
    schema_manifest,
    schema_resource,
    schema_for,
)
from prompt_generator.identity import (
    IdentityError,
    IdentityRegistry,
    ProjectRecord,
    RepositoryBinding,
    canonical_workflow,
    project_id_from_seed,
    qualify_prompt_id,
)


PROJECT = project_id_from_seed("synthetic-project")
SCHEMA_ROOT = Path(__file__).parents[1] / "schemas"


def prompt(local_id: str = "A1", revision: int = 1, **changes):
    value = {
        "schema_version": 1,
        "kind": "prompt",
        "prompt_id": qualify_prompt_id(PROJECT, local_id),
        "local_id": local_id,
        "revision": revision,
        "workflow": "implementation",
        "title": "Synthetic implementation",
        "outcome": {"summary": "Produce a bounded artifact", "success_criteria": ["Artifact is checked"]},
        "scope": {"included": ["Synthetic inputs"], "excluded": ["Live private values"]},
        "authority": {"required": ["Task authorization"], "forbidden": ["Deployment"]},
        "freshness": {"source_revision": "synthetic-1", "checked_at": "2026-01-01T00:00:00Z"},
        "dependencies": {"requires": [], "gates": []},
        "ownership": {"accountable": "synthetic-owner", "writer_scope": ["src/"]},
        "outputs": {"artifacts": ["artifact"], "formats": ["json"]},
        "verification": {"checks": ["unit"]},
        "continuity": {"checkpoint": "save state", "recovery": "resume from state"},
        "finish_line": {"criteria": ["checked"], "owner": "synthetic-owner"},
        "reporting": {"fields": ["summary"]},
        "lineage": {},
        "aliases": [],
        "intentional_gaps": [],
        "repository_bindings": [],
        "provenance": {"source": "synthetic", "source_revision": "synthetic-1", "imported": False},
    }
    value.update(changes)
    return value


class CanonicalContractTests(unittest.TestCase):
    def test_canonical_json_is_sorted_and_has_one_lf(self):
        self.assertEqual(canonical_json_bytes({"z": 1, "a": "x"}), b'{"a":"x","z":1}\n')
        self.assertEqual(canonical_json_loads(b'{"a":"x","z":1}\n'), {"a": "x", "z": 1})

    def test_duplicate_unsafe_version_and_float_are_rejected(self):
        with self.assertRaises(DuplicateKeyError):
            canonical_json_loads(b'{"a":1,"a":2}\n')
        with self.assertRaises(CanonicalJSONError):
            canonical_json_bytes({"value": 1.25})
        with self.assertRaises(UnsupportedVersionError):
            PrivateReferenceRequest("ref_synthetic1", "test", ("read",), schema_version=2)

    def test_private_boundary_has_no_value_field(self):
        request = PrivateReferenceRequest("ref_synthetic1", "test", ("read",))
        result = PrivateReferenceResult("resolved", request.reference, request.purpose, request.scopes, schema="synthetic.v1")
        error = PrivateReferenceError("denied", "not authorized", False, request.reference)
        self.assertNotIn("value", request.to_dict())
        self.assertNotIn("value", result.to_dict())
        self.assertEqual(error.to_dict()["code"], "denied")


class SchemaResourceTests(unittest.TestCase):
    def test_installed_schema_resources_match_public_schema_bytes(self):
        manifest = schema_manifest()
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["kind"], "schema-manifest")
        self.assertEqual(set(manifest["schemas"]), set(SCHEMA_RESOURCE_FILES))
        for kind, filename in SCHEMA_RESOURCE_FILES.items():
            expected = (SCHEMA_ROOT / filename).read_bytes()
            self.assertTrue(schema_resource(kind).is_file())
            self.assertEqual(schema_bytes(kind), expected)
            digest = "sha256:" + hashlib.sha256(expected).hexdigest()
            self.assertEqual(schema_digest(kind), digest)
            self.assertEqual(manifest["schemas"][kind]["digest"], digest)
            self.assertEqual(manifest["schemas"][kind]["bytes"], len(expected))

    def test_runtime_constraints_match_external_prompt_schema(self):
        external = json.loads((SCHEMA_ROOT / "prompt.schema.json").read_text(encoding="utf-8"))
        runtime = schema_for("prompt")
        self.assertEqual(external["properties"]["workflow"]["enum"], list(CANONICAL_WORKFLOWS))
        self.assertEqual(runtime["properties"]["workflow"]["enum"], list(CANONICAL_WORKFLOWS))
        self.assertEqual(
            external["$defs"]["outcome"]["properties"]["success_criteria"]["minItems"],
            runtime["properties"]["outcome"]["properties"]["success_criteria"]["minItems"],
        )
        external_binding_pattern = external["$defs"]["repository_binding"]["properties"]["binding_id"]["pattern"]
        runtime_binding_pattern = runtime["properties"]["repository_bindings"]["items"]["properties"]["binding_id"]["pattern"]
        for value in ("a", "bind-one", "a.b", "P1", "-invalid"):
            self.assertEqual(
                re.fullmatch(external_binding_pattern, value) is not None,
                re.fullmatch(runtime_binding_pattern, value) is not None,
            )


class IdentityTests(unittest.TestCase):
    def test_workflow_aliases_do_not_create_identity(self):
        self.assertEqual(canonical_workflow("build"), "implementation")
        self.assertEqual(qualify_prompt_id(PROJECT, "A1"), f"{PROJECT}:A1")
        with self.assertRaises(IdentityError):
            qualify_prompt_id(PROJECT, "P1")

    def test_bindings_are_separate_from_project_identity(self):
        project = ProjectRecord(PROJECT, "Synthetic")
        registry = IdentityRegistry([project])
        registry.bind_repository(PROJECT, RepositoryBinding("bind-one", "synthetic/repo", "task/a"))
        registry.bind_repository(PROJECT, RepositoryBinding("bind-two", "synthetic/repo", "task/b"))
        self.assertEqual(len(registry.get_project(PROJECT).bindings), 2)
        with self.assertRaises(IdentityError):
            registry.bind_repository("prj_00000000000000000000000000000000", RepositoryBinding("bind-three", "synthetic/repo", "task/a"))


class CatalogTests(unittest.TestCase):
    def test_revision_lookup_alias_and_search_are_deterministic(self):
        first = prompt(aliases=["old-A1"])
        second = prompt(revision=2, title="A later implementation")
        catalog = PromptCatalog([first, second], catalog_id="synthetic")
        self.assertEqual(catalog.lookup(PROJECT + ":A1").revision, 2)
        self.assertEqual(catalog.lookup("old-A1").revision, 2)
        self.assertEqual(catalog.search("later")[0].revision, 2)
        self.assertEqual(catalog.to_bytes(), catalog.to_bytes())

    def test_collisions_and_unknown_fields_fail_closed(self):
        with self.assertRaises(CatalogError):
            PromptCatalog([prompt(), prompt()])
        malformed = prompt(extra="reject")
        with self.assertRaises(Exception):
            validate_catalog({"schema_version": 1, "kind": "catalog", "prompts": [malformed]})


if __name__ == "__main__":
    unittest.main()
