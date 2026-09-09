"""Build or verify the repository's canonical source manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "universal-benchmark-router-source-manifest/v1"
EXCLUDED_PARTS = {".git", ".pytest_cache", ".ruff_cache", "__pycache__", "build", "dist"}


class SourceManifestError(ValueError):
    """The source tree does not match its accepted manifest."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if relative.as_posix() == "SOURCE-MANIFEST.json":
            continue
        if any(part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in relative.parts):
            continue
        if any(part.startswith(".ci-fixtures") for part in relative.parts):
            continue
        result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def build_manifest(root: Path, generated_at: str) -> dict[str, Any]:
    files = _files(root)
    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "hash_algorithm": "sha256",
        "file_count": len(files),
        "files": files,
        "authority": "local_unpublished_source_identity_only",
    }


def verify_manifest(root: Path) -> dict[str, Any]:
    path = root / "SOURCE-MANIFEST.json"
    if not path.is_file() or path.is_symlink():
        raise SourceManifestError("SOURCE-MANIFEST.json is required")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SourceManifestError("source manifest must be JSON") from exc
    expected_keys = {"schema", "generated_at", "hash_algorithm", "file_count", "files", "authority"}
    if set(manifest) != expected_keys or manifest["schema"] != SCHEMA:
        raise SourceManifestError("source manifest shape or schema drift")
    if manifest["hash_algorithm"] != "sha256":
        raise SourceManifestError("source manifest hash algorithm drift")
    if manifest["authority"] != "local_unpublished_source_identity_only":
        raise SourceManifestError("source manifest authority drift")
    observed = _files(root)
    if manifest["file_count"] != len(manifest["files"]):
        raise SourceManifestError("source manifest count drift")
    if manifest["files"] != observed:
        missing = sorted(set(manifest["files"]) - set(observed))
        added = sorted(set(observed) - set(manifest["files"]))
        changed = sorted(
            name for name in set(observed) & set(manifest["files"])
            if observed[name] != manifest["files"][name]
        )
        raise SourceManifestError(f"source drift: missing={missing!r} added={added!r} changed={changed!r}")
    return {
        "schema": "universal-benchmark-router-source-verification/v1",
        "result": "verified",
        "file_count": len(observed),
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "authority": "local_source_identity_verification_only",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--generated-at")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if args.write:
        if not args.generated_at:
            parser.error("--generated-at is required with --write")
        path = root / "SOURCE-MANIFEST.json"
        path.write_bytes(_canonical(build_manifest(root, args.generated_at)))
        result = verify_manifest(root)
    else:
        result = verify_manifest(root)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
