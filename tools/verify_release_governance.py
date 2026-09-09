"""Fail closed on release-workflow or policy authority drift."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


EXPECTED_ACTIONS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/attest": "1e69f48acb82d1966a394da916b4c1698aa569d6",
}
REQUIRED_STEPS = (
    "Verify tag matches package version",
    "Verify exact source manifest",
    "Run portable tests",
    "Run static and bytecode checks",
    "Verify the local-adapter boundary",
    "Generate a CI-only signed fixture bundle",
    "Verify fixture intake and routing non-qualification",
    "Generate two-contributor installed-CLI fixture",
    "Run installed CLI from intake through route selection",
    "Build source and wheel distributions",
    "Verify release contents",
    "Attest exact release artifacts",
    "Create versioned GitHub release",
)


def verify_workflow(source: str) -> dict[str, object]:
    forbidden = ("pull_request:", "pull_request_target:", "workflow_dispatch:", "secrets.", "pypi")
    for marker in forbidden:
        if marker in source.lower():
            raise ValueError(f"forbidden release workflow marker: {marker}")
    required = (
        'github.repository == \'scruge1/universal-benchmark-router\'',
        'tags:\n      - "v*.*.*"',
        "permissions:\n  contents: read",
        "contents: write\n      id-token: write\n      attestations: write",
        "environment:\n      name: release",
        "persist-credentials: false",
        "--no-build-isolation --no-deps .",
        "python -m build --no-isolation --outdir dist",
        'verify_release_tag.py --tag "$GITHUB_REF_NAME"',
        'gh release create "$GITHUB_REF_NAME" dist/* --verify-tag',
    )
    for marker in required:
        if marker not in source:
            raise ValueError(f"missing release workflow control: {marker}")
    uses = dict(re.findall(r"uses:\s*([^@\s]+)@([0-9a-fA-F]+)", source))
    if uses != EXPECTED_ACTIONS:
        raise ValueError(f"release action set or pin drift: {uses!r}")
    positions = [source.find(f"name: {step}") for step in REQUIRED_STEPS]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise ValueError("release verification steps are missing or out of order")
    if source.count("contents: write") != 1 or source.count("id-token: write") != 1:
        raise ValueError("release write authority must occur in exactly one job")
    return {
        "schema": "universal-benchmark-router-release-governance-verification/v1",
        "result": "verified",
        "actions": EXPECTED_ACTIONS,
        "required_step_count": len(REQUIRED_STEPS),
        "authority": "release_governance_source_conformance_only",
    }


def verify_repository(root: Path) -> dict[str, object]:
    for name in ("GOVERNANCE.md", "SECURITY.md", "RELEASING.md", ".github/pull_request_template.md"):
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"required governance file is absent or linked: {name}")
    result = verify_workflow((root / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    result["repository_policy_files"] = 4
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(verify_repository(args.root.resolve(strict=True)), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
