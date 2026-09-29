"""Mechanical publication-safety checks for the public candidate tree."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
FORBIDDEN_MARKERS = ("Personal-Info", "personal-info", "BEGIN PRIVATE KEY", "Authorization: Bearer")


class PublicationSafetyTests(unittest.TestCase):
    def test_public_sources_and_workflow_have_no_private_fetch_or_secret_use(self) -> None:
        paths = [
            *ROOT.joinpath("src").rglob("*.py"),
            *ROOT.joinpath("src/prompt_generator/schemas").rglob("*.json"),
            *ROOT.joinpath("schemas").rglob("*.json"),
            ROOT / "docs/security.md",
            ROOT / "docs/migration.md",
            ROOT / "README.md",
            ROOT / ".github/workflows/ci.yml",
        ]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for marker in FORBIDDEN_MARKERS:
                self.assertNotIn(marker, text, msg=str(path))

    def test_ci_is_public_read_only_and_pinned(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", workflow)
        self.assertIn("astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d", workflow)
        self.assertIn("version: \"0.12.5\"", workflow)
        self.assertIn("python-version: \"3.13\"", workflow)
        self.assertIn("uv run --python 3.13 python", workflow)
        self.assertIn("uv venv --python 3.13", workflow)
        self.assertIn("uv pip install --python", workflow)
        self.assertIn("(cd / &&", workflow)
        self.assertNotIn("uv run --isolated", workflow)
        self.assertNotIn("Personal-Info", workflow)
        self.assertNotIn("secrets.", workflow)


if __name__ == "__main__":
    unittest.main()
