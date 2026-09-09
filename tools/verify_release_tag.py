"""Require an exact semantic-version tag for the configured project version."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


VERSION = re.compile(r'^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$')


def project_version(path: Path) -> str:
    section = None
    versions: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line
            continue
        match = VERSION.fullmatch(line)
        if section == "[project]" and match:
            versions.append(match.group(1))
    if len(versions) != 1:
        raise ValueError("pyproject.toml must declare one strict [project] version")
    return versions[0]


def verify_tag(tag: str, version: str) -> dict[str, str]:
    expected = f"v{version}"
    if tag != expected:
        raise ValueError(f"release tag {tag!r} does not equal {expected!r}")
    return {
        "schema": "universal-benchmark-router-release-tag-verification/v1",
        "result": "verified",
        "tag": tag,
        "version": version,
        "authority": "release_tag_identity_only",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--pyproject", type=Path, default=Path(__file__).resolve().parents[1] / "pyproject.toml")
    args = parser.parse_args()
    result = verify_tag(args.tag, project_version(args.pyproject.resolve(strict=True)))
    import json

    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
