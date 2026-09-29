import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prompt_generator import migration
from prompt_generator.migration import (
    MigrationCollisionError,
    MigrationError,
    UnsafeMigrationPathError,
    copy_then_verify,
    dry_run_import,
)


class MigrationTests(unittest.TestCase):
    def test_dry_run_then_copy_preserves_source_and_verifies(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source"
            destination = Path(root) / "destination"
            source.mkdir()
            original = b'{"prompt_id":"prj_00000000000000000000000000000001:A1","revision":1}\n'
            (source / "prompt.json").write_bytes(original)
            report = dry_run_import(source, destination)
            self.assertTrue(report.ok)
            self.assertFalse(destination.exists())
            applied = copy_then_verify(source, destination)
            self.assertTrue(applied.verified)
            self.assertEqual((source / "prompt.json").read_bytes(), original)
            self.assertEqual((destination / "prompt.json").read_bytes(), original)

    def test_different_existing_target_is_reported_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source"
            destination = Path(root) / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "payload.txt").write_text("source", encoding="utf-8")
            (destination / "payload.txt").write_text("other", encoding="utf-8")
            report = dry_run_import(source, destination)
            self.assertFalse(report.ok)
            with self.assertRaises(MigrationCollisionError):
                copy_then_verify(source, destination)
            self.assertEqual((destination / "payload.txt").read_text(encoding="utf-8"), "other")

    def test_single_file_destination_preserves_explicit_rename(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            source = root_path / "source.txt"
            destination = root_path / "renamed.txt"
            source.write_bytes(b"source")

            plan = dry_run_import(source, destination)
            applied = copy_then_verify(source, destination, plan=plan)

            self.assertTrue(applied.verified)
            self.assertEqual(destination.read_bytes(), b"source")
            self.assertEqual(source.read_bytes(), b"source")

    def test_symlink_component_is_rejected_without_following_escape(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            source = root_path / "source"
            outside = root_path / "outside"
            source.mkdir()
            outside.mkdir()
            (source / "payload.txt").write_bytes(b"source")
            linked_destination = root_path / "linked-destination"
            linked_destination.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(UnsafeMigrationPathError):
                dry_run_import(source, linked_destination / "payload.txt")
            self.assertFalse((outside / "payload.txt").exists())

    def test_apply_uses_exact_plan_without_recomputing(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            source = root_path / "source"
            destination = root_path / "destination"
            source.mkdir()
            (source / "payload.txt").write_bytes(b"source")
            plan = dry_run_import(source, destination)

            with mock.patch.object(migration, "dry_run_import", side_effect=AssertionError("recomputed")):
                applied = copy_then_verify(source, destination, plan=plan)
            self.assertTrue(applied.verified)
            self.assertEqual((destination / "payload.txt").read_bytes(), b"source")

    def test_source_mutation_after_plan_is_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            source = root_path / "source"
            destination = root_path / "destination"
            source.mkdir()
            payload = source / "payload.txt"
            payload.write_bytes(b"before")
            plan = dry_run_import(source, destination)
            payload.write_bytes(b"after")

            with self.assertRaises(MigrationError):
                copy_then_verify(source, destination, plan=plan)
            self.assertFalse(destination.exists())

    def test_new_source_file_after_plan_is_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            source = root_path / "source"
            destination = root_path / "destination"
            source.mkdir()
            (source / "payload.txt").write_bytes(b"source")
            plan = dry_run_import(source, destination)
            (source / "new.txt").write_bytes(b"new")

            with self.assertRaises(MigrationError):
                copy_then_verify(source, destination, plan=plan)
            self.assertFalse(destination.exists())

    def test_race_collision_never_clobbers_competing_target(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            source = root_path / "source"
            destination = root_path / "destination"
            source.mkdir()
            (source / "payload.txt").write_bytes(b"source")
            plan = dry_run_import(source, destination)
            destination.mkdir()

            def race(temporary: Path, target: Path) -> bool:
                target.write_bytes(b"racer")
                return False

            with mock.patch.object(migration, "_publish_no_clobber", side_effect=race):
                with self.assertRaises(MigrationCollisionError):
                    copy_then_verify(source, destination, plan=plan)
            self.assertEqual((destination / "payload.txt").read_bytes(), b"racer")


if __name__ == "__main__":
    unittest.main()
