from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.generate_ci_fixture_bundle import write_bundle
from tools.verify_ci_fixture_bundle import verify_bundle
from tools.verify_release_contents import inspect_wheel


ROOT = Path(__file__).parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


class RepositoryContractTests(unittest.TestCase):
    def test_workflow_has_read_only_full_sha_pins_and_safe_trigger(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", source)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", source)
        self.assertIn("actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97", source)
        self.assertIn("persist-credentials: false", source)
        self.assertNotIn("pull_request_target", source)
        self.assertNotIn("secrets.", source)
        self.assertNotIn("self-hosted", source)
        self.assertNotIn("publish", source.lower())

    def test_ci_fixture_is_accepted_but_cannot_qualify_for_routing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "fixture"
            write_bundle(bundle)
            result = verify_bundle(bundle)
        self.assertEqual(4, result["accepted_identity_count"])
        self.assertEqual(0, result["qualified_entry_count"])
        self.assertFalse(result["contains_private_key"])

    def test_release_verifier_rejects_a_local_adapter_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.whl"
            with zipfile.ZipFile(path, "w") as archive:
                for module in (
                    "universal_model_router.py",
                    "universal_benchmark_exchange.py",
                    "universal_benchmark_registry.py",
                    "universal_router_cli.py",
                ):
                    archive.writestr(module, "")
                archive.writestr("dashboard/local.py", "")
            with self.assertRaisesRegex(ValueError, "forbidden wheel path"):
                inspect_wheel(path)

    def test_release_verifier_rejects_private_key_material(self) -> None:
        marker = b"-----BEGIN " + b"PRIVATE KEY-----"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.whl"
            with zipfile.ZipFile(path, "w") as archive:
                for module in (
                    "universal_model_router.py",
                    "universal_benchmark_exchange.py",
                    "universal_benchmark_registry.py",
                    "universal_router_cli.py",
                ):
                    archive.writestr(module, marker if module == "universal_model_router.py" else b"")
            with self.assertRaisesRegex(ValueError, "private key material"):
                inspect_wheel(path)


if __name__ == "__main__":
    unittest.main()
