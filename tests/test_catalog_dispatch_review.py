import unittest

from prompt_generator.catalog import PromptCatalog
from prompt_generator.contracts import canonical_json_loads
from prompt_generator.dispatch import (
    Carrier,
    DispatchError,
    DispatchLedger,
    DispatchRequest,
    DispatchRecord,
    DuplicateDispatchError,
    NonDispatchableCarrierError,
    payload_digest,
)
from prompt_generator.identity import project_id_from_seed, qualify_prompt_id
from prompt_generator.reviews import ReviewError, ReviewLedger, digest


PROJECT = project_id_from_seed("catalog-dispatch-review-fixture")


def prompt(local_id="A1", revision=1, *, lineage=None, aliases=None, title="Synthetic prompt"):
    return {
        "schema_version": 1,
        "kind": "prompt",
        "prompt_id": qualify_prompt_id(PROJECT, local_id),
        "local_id": local_id,
        "revision": revision,
        "workflow": "implementation",
        "title": title,
        "outcome": {"summary": "Make one bounded artifact", "success_criteria": ["Artifact exists"]},
        "scope": {"included": ["Synthetic data"], "excluded": ["Remote execution"]},
        "authority": {"required": ["Task authorization"], "forbidden": ["Deployment"]},
        "freshness": {"source_revision": "fixture-1", "checked_at": "2026-01-01T00:00:00Z"},
        "dependencies": {"requires": [], "gates": []},
        "ownership": {"accountable": "fixture-owner", "writer_scope": ["src/"]},
        "outputs": {"artifacts": ["artifact"], "formats": ["json"]},
        "verification": {"checks": ["unit"]},
        "continuity": {"checkpoint": "record state", "recovery": "resume state"},
        "finish_line": {"criteria": ["checked"], "owner": "fixture-owner"},
        "reporting": {"fields": ["summary"]},
        "lineage": lineage or {},
        "aliases": aliases or [],
        "intentional_gaps": [],
        "repository_bindings": [],
        "provenance": {"source": "synthetic", "source_revision": "fixture-1", "imported": False},
    }


class DispatchAndReviewTests(unittest.TestCase):
    def test_alias_revision_carrier_and_lineage_guards(self):
        old = prompt(aliases=["old-A1"])
        correction = prompt("B1", lineage={"corrects": [old["prompt_id"]]})
        catalog = PromptCatalog([old, correction])
        ledger = DispatchLedger()
        carrier = Carrier("carrier-a")
        ledger.record("old-A1", 1, carrier, payload="payload", catalog=catalog)
        with self.assertRaises(DuplicateDispatchError):
            ledger.record(old["prompt_id"], 1, carrier, payload="payload", catalog=catalog)
        with self.assertRaises(DuplicateDispatchError):
            ledger.record(correction["prompt_id"], 1, carrier, payload="new", catalog=catalog)
        self.assertTrue(ledger.check(correction["prompt_id"], 1, Carrier("carrier-b"), payload="new", catalog=catalog))
        self.assertEqual(ledger.records[0].payload_digest, payload_digest("payload"))

    def test_review_is_bound_to_exact_source_and_payload(self):
        prompt_id = qualify_prompt_id(PROJECT, "A2")
        ledger = ReviewLedger()
        ledger.record(
            prompt_id=prompt_id,
            revision=1,
            source_revision="source-1",
            payload_digest=digest("payload-1"),
            source_digest=digest("whole-1"),
            reviewer="reviewer-a",
        )
        self.assertTrue(ledger.is_valid(prompt_id, 1, source_revision="source-1", payload_digest=digest("payload-1"), source_digest=digest("whole-1"), reviewer="reviewer-a"))
        self.assertFalse(ledger.is_valid(prompt_id, 1, source_revision="source-2", payload_digest=digest("payload-1"), source_digest=digest("whole-1"), reviewer="reviewer-a"))
        self.assertEqual(len(ledger.invalidate_if_changed(prompt_id, 1, source_revision="source-2", payload_digest=digest("payload-1"))), 1)

    def test_review_notes_round_trip_accepts_empty_and_populated_arrays(self):
        empty = ReviewLedger()
        empty.record(
            prompt_id=qualify_prompt_id(PROJECT, "A5"),
            revision=1,
            source_revision="source-1",
            payload_digest=digest("payload-empty"),
            reviewer="reviewer-a",
            notes=[],
        )
        empty_document = empty.to_dict()
        self.assertNotIn("notes", empty_document["entries"][0])
        restored_empty = ReviewLedger.from_dict(empty_document)
        self.assertEqual(restored_empty.to_dict(), empty_document)
        self.assertEqual(
            ReviewLedger.from_dict(canonical_json_loads(empty.to_bytes())).to_bytes(),
            empty.to_bytes(),
        )

        populated = ReviewLedger()
        populated.record(
            prompt_id=qualify_prompt_id(PROJECT, "A6"),
            revision=1,
            source_revision="source-1",
            payload_digest=digest("payload-populated"),
            reviewer="reviewer-a",
            notes=["first note", "second note"],
        )
        populated_document = populated.to_dict()
        self.assertEqual(populated_document["entries"][0]["notes"], ["first note", "second note"])
        restored_populated = ReviewLedger.from_dict(populated_document)
        self.assertEqual(restored_populated.to_dict(), populated_document)
        self.assertEqual(
            ReviewLedger.from_dict(canonical_json_loads(populated.to_bytes())).to_bytes(),
            populated.to_bytes(),
        )

        malformed = {"entries": [dict(populated_document["entries"][0])]}
        malformed["entries"][0]["notes"] = "not-an-array"
        with self.assertRaises(ReviewError):
            ReviewLedger.from_dict({**populated_document, "entries": malformed["entries"]})

    def test_dispatch_and_carrier_wire_types_are_strict(self):
        with self.assertRaises(NonDispatchableCarrierError):
            Carrier.from_value({"carrier_id": "carrier-a", "dispatchable": "false"})
        request = {
            "schema_version": 1,
            "kind": "dispatch-request",
            "prompt_id": qualify_prompt_id(PROJECT, "A3"),
            "revision": 1,
            "carrier": {"carrier_id": "carrier-a", "dispatchable": False},
            "payload": "payload",
        }
        record = DispatchRecord.from_request(DispatchRequest.from_value(request)).to_dict()
        with self.assertRaises(DispatchError):
            DispatchLedger.from_dict({"schema_version": True, "kind": "dispatch-ledger", "records": []})
        record["schema_version"] = True
        with self.assertRaises(DispatchError):
            DispatchLedger.from_dict({"schema_version": 1, "kind": "dispatch-ledger", "records": [record]})

    def test_review_wire_types_are_strict(self):
        prompt_id = qualify_prompt_id(PROJECT, "A4")
        ledger = ReviewLedger()
        ledger.record(
            prompt_id=prompt_id,
            revision=1,
            source_revision="source-1",
            payload_digest=digest("payload-1"),
            reviewer="reviewer-a",
        )
        document = ledger.to_dict()
        document["schema_version"] = True
        with self.assertRaises(ReviewError):
            ReviewLedger.from_dict(document)
        document = ledger.to_dict()
        document["entries"][0]["kind"] = "review-record"
        with self.assertRaises(ReviewError):
            ReviewLedger.from_dict(document)


if __name__ == "__main__":
    unittest.main()
