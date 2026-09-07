from __future__ import annotations

import importlib
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "jindiao"
EXPECTED_SUBPACKAGES = (
    "agents",
    "api",
    "application",
    "contracts",
    "deepsearch",
    "evaluation",
    "observability",
    "orchestration",
    "reporting",
    "risk",
    "scenarios",
    "skills",
    "tianyancha",
)


class PackageBoundaryTests(unittest.TestCase):
    def test_design_subpackages_are_explicit_python_packages(self) -> None:
        missing = [
            name
            for name in EXPECTED_SUBPACKAGES
            if not (PACKAGE_ROOT / name / "__init__.py").is_file()
        ]

        self.assertEqual([], missing)

    def test_public_packages_declare_exports(self) -> None:
        for name in EXPECTED_SUBPACKAGES:
            module = importlib.import_module(f"jindiao.{name}")
            with self.subTest(package=name):
                self.assertIsInstance(module.__all__, list)

    def test_root_package_exports_version(self) -> None:
        package = importlib.import_module("jindiao")

        self.assertIn("__version__", package.__all__)


if __name__ == "__main__":
    unittest.main()
