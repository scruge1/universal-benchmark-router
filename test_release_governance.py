from __future__ import annotations

import unittest
from pathlib import Path

from tools.verify_release_governance import verify_repository, verify_workflow
from tools.verify_release_tag import project_version, verify_tag


ROOT = Path(__file__).parent
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


class ReleaseGovernanceTests(unittest.TestCase):
    def test_repository_governance_passes(self) -> None:
        result = verify_repository(ROOT)
        self.assertEqual("verified", result["result"])
        self.assertEqual(11, result["required_step_count"])

    def test_tag_must_equal_project_version(self) -> None:
        version = project_version(ROOT / "pyproject.toml")
        self.assertEqual("v0.1.0", verify_tag("v0.1.0", version)["tag"])
        with self.assertRaisesRegex(ValueError, "does not equal"):
            verify_tag("v0.1.1", version)

    def test_release_rejects_pull_request_authority(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8") + "\npull_request:\n"
        with self.assertRaisesRegex(ValueError, "forbidden"):
            verify_workflow(source)

    def test_release_rejects_floating_action_pin(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8").replace(
            "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6",
            "actions/attest@4",
        )
        with self.assertRaisesRegex(ValueError, "pin drift"):
            verify_workflow(source)

    def test_release_rejects_missing_version_binding(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8").replace(
            'verify_release_tag.py --tag "$GITHUB_REF_NAME"',
            "verify_release_tag.py",
        )
        with self.assertRaisesRegex(ValueError, "missing release workflow control"):
            verify_workflow(source)


if __name__ == "__main__":
    unittest.main()
