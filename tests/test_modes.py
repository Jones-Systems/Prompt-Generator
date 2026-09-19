"""Bounded mode-contract tests using synthetic nondispatchable fixtures."""

from __future__ import annotations

import json
import hashlib
from importlib import resources
from pathlib import Path
import unittest

from prompt_generator.modes import (
    LEVEL_1,
    LEVEL_2,
    LEVEL_3,
    ModeActivationError,
    ModeBindingError,
    ModeResumeError,
    ModeValidationError,
    load_mode_template,
    parse_mode_template,
    project_request_digest,
    render_mode_template,
    validate_level_three,
    validate_mode,
)


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "examples" / "synthetic"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ModeContractTests(unittest.TestCase):
    def test_each_versioned_mode_is_explicit_and_nondispatchable(self):
        for name, mode in (
            ("level-1.v1.json", LEVEL_1),
            ("level-2.v1.json", LEVEL_2),
            ("level-3.v1.json", LEVEL_3),
        ):
            document = validate_mode(fixture(name))
            self.assertEqual(document["mode"], mode)
            self.assertFalse(document["dispatchable"])

    def test_templates_keep_composer_instruction_outside_payload(self):
        for mode in (LEVEL_1, LEVEL_2, LEVEL_3):
            document = load_mode_template(mode, root=ROOT)
            rendered = render_mode_template(document, ephemeral=(mode == LEVEL_3))
            self.assertEqual(parse_mode_template(rendered), document)
            payload = rendered.split("```json\n", 1)[1].split("\n```", 1)[0]
            self.assertNotIn("@Web search", payload)

    def test_packaged_templates_are_available_without_checkout_lookup(self):
        for mode in (LEVEL_1, LEVEL_2, LEVEL_3):
            resource = resources.files("prompt_generator").joinpath("templates", f"{mode}.md")
            self.assertTrue(resource.is_file())
            self.assertEqual(resource.read_bytes(), (ROOT / "templates" / f"{mode}.md").read_bytes())

    def test_level_one_requires_explicit_fallback(self):
        document = fixture("level-1.v1.json")
        document["activation"]["explicit_fallback"] = False
        with self.assertRaises(ModeActivationError):
            validate_mode(document)

    def test_level_two_requires_intake_and_rejects_cross_mode_handle(self):
        document = fixture("level-2.v1.json")
        document["instructions"]["complete"] = False
        with self.assertRaises(ModeActivationError):
            validate_mode(document)
        document = fixture("level-2.v1.json")
        document["workspace_handle"] = "ws-01.AAAAAAAAAAAAAAAAAAAAAA"
        with self.assertRaises(ModeValidationError):
            validate_mode(document)

    def test_level_three_requires_exact_app_and_project_request_shape(self):
        document = fixture("level-3.v1.json")
        document["app"]["name"] = "ChatGPT Web Connector — Research"
        with self.assertRaises(ModeBindingError):
            validate_mode(document)
        document = fixture("level-3.v1.json")
        document["project_request"]["connector_tools"] = ["read"]
        with self.assertRaises(ModeBindingError):
            validate_mode(document)

    def test_unknown_effect_and_stale_resume_fail_closed(self):
        document = fixture("level-2.v1.json")
        document["recovery"]["effect"] = "unknown"
        document["recovery"]["status"] = "unknown"
        with self.assertRaises(ModeResumeError):
            validate_mode(document)
        document = fixture("level-3.v1.json")
        document["resume"]["fresh"] = False
        with self.assertRaises(ModeResumeError):
            validate_mode(document)

    def test_level_three_resume_requires_a_new_handle_and_bound_previous_digest(self):
        document = fixture("level-3.v1.json")
        request = document["project_request"]
        digest = project_request_digest(request)
        previous_handle = "ws-01.AQEBAQEBAQEBAQEBAQEBAQ"
        document["resume"].update(
            {
                "kind": "resume",
                "project_request_sha256": digest,
                "previous_workspace_handle_sha256": hashlib.sha256(previous_handle.encode()).hexdigest(),
                "fresh": True,
            }
        )
        validate_level_three(document, previous_workspace_handle=previous_handle)
        document["resume"]["previous_workspace_handle_sha256"] = hashlib.sha256(
            request["workspace_handle"].encode()
        ).hexdigest()
        with self.assertRaises(ModeResumeError):
            validate_level_three(document, previous_workspace_handle=request["workspace_handle"])

    def test_level_three_render_is_explicitly_ephemeral(self):
        document = fixture("level-3.v1.json")
        from prompt_generator.modes import render_project_request

        with self.assertRaises(ModeBindingError):
            render_mode_template(document)
        with self.assertRaises(ModeBindingError):
            render_project_request(document["project_request"])
        rendered = render_project_request(document["project_request"], ephemeral=True)
        self.assertEqual(
            json.loads(rendered)["workspace_handle"],
            "ws-01.AAAAAAAAAAAAAAAAAAAAAA",
        )


if __name__ == "__main__":
    unittest.main()
