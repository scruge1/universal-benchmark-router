"""Fail closed when a built distribution contains undeclared local files."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import zipfile
from pathlib import Path


MODULES = {
    "universal_model_router.py",
    "universal_benchmark_exchange.py",
    "universal_benchmark_registry.py",
    "universal_router_cli.py",
}
WHEEL_ASSETS = {
    "router/README.md",
    "router/__init__.py",
    "router/benchmark-capability-contract-v2.json",
    "router/standard-task-suite-v1.json",
}
SDIST_FILES = {
    "AGENTS.md",
    "CONTRIBUTING-PORTABLE.md",
    "GOVERNANCE.md",
    "LICENSE",
    "MANIFEST.in",
    "PKG-INFO",
    "README.md",
    "RELEASING.md",
    "SECURITY.md",
    "SOURCE-MANIFEST.json",
    "pyproject.toml",
    "requirements-ci.txt",
    "setup.cfg",
    "test_repository_contract.py",
    "test_release_governance.py",
    "test_end_to_end_cli.py",
    "test_universal_benchmark_exchange.py",
    "test_universal_benchmark_registry.py",
    "test_universal_model_router.py",
    "test_verify_portable_router_boundary.py",
    "verify_portable_router_boundary.py",
    *MODULES,
}
SDIST_PREFIXES = (
    ".github/",
    "examples/",
    "router/",
    "router-schemas/",
    "tools/",
    "universal_benchmark_router.egg-info/",
)
FORBIDDEN_PATH_MARKERS = (
    "dashboard", "gpu_lighting", "lifecycle", "model_control", "paired-harness", "thermal", "workload"
)
PRIVATE_KEY_MARKERS = (
    b"-----BEGIN " + b"PRIVATE KEY-----",
    b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----",
)


def _contains_private_key(value: bytes) -> bool:
    return any(marker in value for marker in PRIVATE_KEY_MARKERS)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_wheel(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        files = [name for name in names if not name.endswith("/")]
        for name in files:
            if any(marker in name for marker in FORBIDDEN_PATH_MARKERS):
                raise ValueError(f"forbidden wheel path: {name}")
            if name not in MODULES and name not in WHEEL_ASSETS and ".dist-info/" not in name:
                raise ValueError(f"unexpected wheel path: {name}")
            value = archive.read(name)
            if _contains_private_key(value):
                raise ValueError(f"private key material in wheel: {name}")
        if not MODULES <= set(files):
            raise ValueError("wheel is missing a portable module")
        if not WHEEL_ASSETS <= set(files):
            raise ValueError("wheel is missing a bundled contract asset")
        return len(files)


def inspect_sdist(path: Path) -> int:
    with tarfile.open(path) as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        roots = {member.name.split("/", 1)[0] for member in members}
        if len(roots) != 1:
            raise ValueError("source archive must have one root")
        root = next(iter(roots))
        relative_names: set[str] = set()
        for member in members:
            relative = member.name[len(root) + 1:]
            relative_names.add(relative)
            if any(marker in relative for marker in FORBIDDEN_PATH_MARKERS):
                raise ValueError(f"forbidden source path: {relative}")
            if relative not in SDIST_FILES and not relative.startswith(SDIST_PREFIXES):
                raise ValueError(f"unexpected source path: {relative}")
            stream = archive.extractfile(member)
            value = b"" if stream is None else stream.read()
            if _contains_private_key(value):
                raise ValueError(f"private key material in source archive: {relative}")
        required = MODULES | {"README.md", "LICENSE", "SOURCE-MANIFEST.json", "tools/verify_ci_fixture_bundle.py"}
        if not required <= relative_names:
            raise ValueError("source archive is missing a required portable file")
        return len(members)


def verify_distributions(directory: Path) -> dict[str, object]:
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("exactly one wheel and one source archive are required")
    return {
        "schema": "universal-benchmark-router-release-content-verification/v1",
        "result": "verified",
        "wheel_sha256": _digest(wheels[0]),
        "wheel_file_count": inspect_wheel(wheels[0]),
        "source_sha256": _digest(sdists[0]),
        "source_file_count": inspect_sdist(sdists[0]),
        "authority": "offline_release_content_verification_only",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify_distributions(args.dist.resolve(strict=True)), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
