"""Public boundary checks use only opaque synthetic references and handles."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from prompt_generator.contracts import PrivateReferenceError, PrivateReferenceRequest, PrivateReferenceResult
from prompt_generator.modes import ModeBindingError, render_mode_template, render_project_request


ROOT = Path(__file__).parents[1]


class PublicPrivateBoundaryTests(unittest.TestCase):
    def test_public_private_contracts_have_no_resolved_value(self) -> None:
        request = PrivateReferenceRequest("ref_synthetic01", "synthetic test", ("read",))
        result = PrivateReferenceResult("resolved", request.reference, request.purpose, request.scopes, schema="synthetic.v1")
        error = PrivateReferenceError("denied", "synthetic denial", False, request.reference)
        for value in (request.to_dict(), result.to_dict(), error.to_dict()):
            self.assertNotIn("value", value)
            self.assertNotIn("resolved_value", value)

    def test_checked_in_examples_are_synthetic_and_nondispatchable(self) -> None:
        for path in sorted((ROOT / "examples").rglob("*.json")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("Personal-Info", text)
            value = json.loads(text)
            if "dispatchable" in value:
                self.assertFalse(value["dispatchable"])

    def test_level_three_render_requires_explicit_ephemeral_authorization(self) -> None:
        value = json.loads((ROOT / "examples/synthetic/level-3.v1.json").read_text(encoding="utf-8"))
        with self.assertRaises(ModeBindingError):
            render_mode_template(value)
        with self.assertRaises(ModeBindingError):
            render_project_request(value["project_request"])
        rendered_mode = render_mode_template(value, ephemeral=True)
        self.assertIn('"workspace_handle"', rendered_mode)
        rendered = render_project_request(value["project_request"], ephemeral=True)
        self.assertIn('"workspace_handle"', rendered)


if __name__ == "__main__":
    unittest.main()
