import unittest

from prompt_generator.coordination import (
    CoordinationError,
    CoordinationUnavailableError,
    require_coordination,
    validate_coordination,
    validate_sibling_or_stack,
    validate_stack,
)


class CoordinationTests(unittest.TestCase):
    def test_fictional_silent_and_unavailable_gates_fail_closed(self):
        decision = validate_coordination({"coordinator": "fictional-coordinator", "participants": ["fictional-coordinator"], "required_participants": ["review"], "gates": {}})
        self.assertFalse(decision)
        with self.assertRaises(CoordinationUnavailableError):
            require_coordination({"coordinator": "real", "participants": ["real"], "required_participants": ["review"], "gates": {"review": "unavailable"}})

    def test_duplicate_gate_ids_are_rejected_in_both_orders(self):
        for statuses in (("pass", "fail"), ("fail", "pass")):
            with self.assertRaisesRegex(CoordinationError, "duplicate gate_id: review"):
                validate_coordination({
                    "coordinator": "real",
                    "participants": ["real", "review"],
                    "required_participants": ["review"],
                    "gates": [
                        {"gate_id": "review", "status": statuses[0]},
                        {"gate_id": "review", "status": statuses[1]},
                    ],
                })

    def test_sibling_default_and_stack_cumulative_parent_contract(self):
        self.assertTrue(validate_sibling_or_stack({"mode": "sibling"}))
        self.assertFalse(validate_sibling_or_stack({"mode": "sibling", "depends_on": "base"}))
        layers = [
            {"branch": "task/base", "oid": "aaa", "layer": 1, "path_prefix": "src/base", "checks": ["base"], "cumulative_checks": ["base"]},
            {"branch": "task/child", "oid": "bbb", "layer": 2, "path_prefix": "src/base/child", "parent_branch": "task/base", "parent_oid": "aaa", "parent_current_oid": "aaa", "checks": ["child"], "cumulative_checks": ["base", "child"]},
        ]
        self.assertTrue(validate_stack(layers))
        layers[1]["parent_current_oid"] = "changed"
        self.assertFalse(validate_stack(layers))

    def test_stack_requires_current_parent_oid_and_rejects_boolean_coercion(self):
        layers = [
            {"branch": "task/base", "oid": "aaa", "layer": 1, "path_prefix": "src/base", "checks": ["base"], "cumulative_checks": ["base"]},
            {"branch": "task/child", "oid": "bbb", "layer": 2, "path_prefix": "src/base/child", "parent_branch": "task/base", "parent_oid": "aaa", "checks": ["child"], "cumulative_checks": ["base", "child"]},
        ]
        decision = validate_stack(layers)
        self.assertFalse(decision)
        self.assertIn("missing current parent OID", " ".join(decision.failures))
        layers[1]["parent_current_oid"] = "aaa"
        layers[1]["parent_invalidated"] = "false"
        with self.assertRaises(CoordinationError):
            validate_stack(layers)


if __name__ == "__main__":
    unittest.main()
