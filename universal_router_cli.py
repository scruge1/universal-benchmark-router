"""Contributor command line for offline universal benchmark evidence."""

from __future__ import annotations

import argparse
import importlib.resources
import json
import os
import sys
from pathlib import Path
from typing import Any

from universal_benchmark_exchange import (
    compile_catalog,
    select_route_with_universal_evidence,
    verify_acknowledgement,
    verify_benchmark_request,
    verify_contribution,
    verify_result_envelope,
)
from universal_benchmark_registry import FileRegistry, RegistryError, sha256_bytes, strict_json_bytes
from universal_model_router import canonical_sha256


ASSET_PACKAGE = "router"
ASSET_FILES = (
    "benchmark-capability-contract-v2.json",
    "standard-task-suite-v1.json",
)


def _load(path: Path) -> tuple[Any, str]:
    value = path.read_bytes()
    return strict_json_bytes(value), sha256_bytes(value)


def _emit(value: Any, *, stream: Any = sys.stdout) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")), file=stream)


def _write_absent(path: Path, value: Any) -> dict[str, Any]:
    parent = path.parent.resolve(strict=True)
    target = parent / path.name
    if target.exists() or target.is_symlink():
        raise RegistryError(f"output must be absent: {target}")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(target, flags, 0o444)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"path": str(target), "sha256": sha256_bytes(payload)}


def _result_or_output(value: dict[str, Any], output: Path | None, document_type: str) -> dict[str, Any]:
    if output is None:
        return value
    saved = _write_absent(output, value)
    return {
        "result": "saved",
        "document_type": document_type,
        **saved,
        "authority": "offline_saved_candidate_only",
    }


def _export_contracts(output_dir: Path) -> dict[str, Any]:
    if output_dir.exists() or output_dir.is_symlink():
        raise RegistryError(f"output directory must be absent: {output_dir}")
    output_dir.mkdir(parents=False)
    hashes: dict[str, str] = {}
    try:
        root = importlib.resources.files(ASSET_PACKAGE)
        for relative in ASSET_FILES:
            source = root.joinpath(*relative.split("/"))
            payload = source.read_bytes()
            target = output_dir.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            hashes[relative] = sha256_bytes(payload)
        manifest = {
            "schema": "universal-benchmark-exported-contracts/v1",
            "files": dict(sorted(hashes.items())),
            "authority": "read_only_contract_assets_only",
        }
        manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        (output_dir / "manifest.json").write_bytes(manifest_bytes)
    except BaseException:
        for child in sorted(output_dir.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        output_dir.rmdir()
        raise
    return {
        "result": "exported",
        "output_dir": str(output_dir.resolve()),
        "file_count": len(hashes),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "authority": "read_only_contract_assets_only",
    }


def _common_files(parser: argparse.ArgumentParser, *names: str) -> None:
    for name in names:
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="universal-router")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("registry-init")
    init.add_argument("--root", type=Path, required=True)
    audit = commands.add_parser("registry-audit")
    audit.add_argument("--root", type=Path, required=True)
    export = commands.add_parser("export-contracts", help="Export the bundled suite and capability contract")
    export.add_argument("--output-dir", type=Path, required=True)
    catalog = commands.add_parser("compile-catalog", help="Compile accepted registry evidence into a catalog candidate")
    catalog.add_argument("--root", type=Path, required=True)
    catalog.add_argument("--suite", type=Path, required=True)
    catalog.add_argument("--generation", type=int, required=True)
    catalog.add_argument("--compiled-at", required=True)
    catalog.add_argument("--output", type=Path)
    route = commands.add_parser("route", help="Select from saved local profiles using a universal evidence catalog")
    route.add_argument("--request", type=Path, required=True)
    route.add_argument("--suite", type=Path, required=True)
    route.add_argument("--catalog", type=Path, required=True)
    route.add_argument("--profile", type=Path, action="append", required=True)
    route.add_argument("--output", type=Path)
    for action in ("validate", "ingest"):
        request = commands.add_parser(f"{action}-request")
        _common_files(request, "request", "capability_contract", "public_keys")
        if action == "ingest":
            request.add_argument("--root", type=Path, required=True)
        contribution = commands.add_parser(f"{action}-contribution")
        _common_files(contribution, "contribution", "suite", "public_keys")
        if action == "ingest":
            contribution.add_argument("--root", type=Path, required=True)
        result = commands.add_parser(f"{action}-result")
        _common_files(result, "result", "request", "capability_contract", "suite", "public_keys")
        if action == "ingest":
            result.add_argument("--root", type=Path, required=True)
        acknowledgement = commands.add_parser(f"{action}-acknowledgement")
        _common_files(acknowledgement, "acknowledgement", "contribution", "suite", "public_keys")
        if action == "ingest":
            acknowledgement.add_argument("--root", type=Path, required=True)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "registry-init":
        registry = FileRegistry.initialize(args.root)
        return {"result": "initialized", "root": str(registry.root), "authority": "offline_saved_evidence_only"}
    if args.command == "registry-audit":
        return FileRegistry(args.root).audit()
    if args.command == "export-contracts":
        return _export_contracts(args.output_dir)
    if args.command == "compile-catalog":
        if args.generation < 1:
            raise RegistryError("generation must be a positive integer")
        suite, _ = _load(args.suite)
        suite_sha256 = canonical_sha256(suite)
        registry = FileRegistry(args.root)
        contributions = [
            item for item in registry.accepted_documents("contribution")
            if item["suite_id"] == suite["suite_id"] and item["suite_sha256"] == suite_sha256
        ]
        submission_ids = {item["submission_id"] for item in contributions}
        acknowledgements = [
            item for item in registry.accepted_documents("acknowledgement")
            if item["submission_id"] in submission_ids
        ]
        catalog = compile_catalog(
            suite,
            contributions,
            acknowledgements,
            generation=args.generation,
            compiled_at=args.compiled_at,
        )
        return _result_or_output(catalog, args.output, "universal_model_evidence_catalog")
    if args.command == "route":
        request, _ = _load(args.request)
        suite, _ = _load(args.suite)
        catalog, _ = _load(args.catalog)
        profiles = [_load(path)[0] for path in args.profile]
        decision = select_route_with_universal_evidence(request, profiles, suite, catalog)
        return _result_or_output(decision, args.output, "universal_route_decision")
    action, document_type = args.command.split("-", 1)
    public_keys, _ = _load(args.public_keys)
    loaded = {name: _load(getattr(args, name)) for name in vars(args) if name not in {"command", "root", "public_keys"}}
    values = {name: item[0] for name, item in loaded.items()}
    source_sha256 = loaded[document_type][1]
    if document_type == "request":
        verify_benchmark_request(values["request"], values["capability_contract"], public_keys)
        if action == "validate":
            claim = None
        else:
            claim = FileRegistry(args.root).ingest_request(
                values["request"], values["capability_contract"], public_keys, source_sha256=source_sha256
            )
    elif document_type == "contribution":
        verify_contribution(values["contribution"], values["suite"], public_keys)
        if action == "validate":
            claim = None
        else:
            claim = FileRegistry(args.root).ingest_contribution(
                values["contribution"], values["suite"], public_keys, source_sha256=source_sha256
            )
    elif document_type == "result":
        verify_result_envelope(
            values["result"], values["request"], values["capability_contract"], values["suite"], public_keys
        )
        if action == "validate":
            claim = None
        else:
            claim = FileRegistry(args.root).ingest_result(
                values["result"], values["request"], values["capability_contract"], values["suite"], public_keys,
                source_sha256=source_sha256,
            )
    else:
        verify_acknowledgement(
            values["acknowledgement"], values["contribution"], values["suite"], public_keys
        )
        if action == "validate":
            claim = None
        else:
            claim = FileRegistry(args.root).ingest_acknowledgement(
                values["acknowledgement"], values["contribution"], values["suite"], public_keys,
                source_sha256=source_sha256,
            )
    if action == "validate":
        return {"result": "verified", "document_type": document_type, "authority": "offline_validation_only"}
    return {"result": "accepted", "claim": claim}


def main(argv: list[str] | None = None) -> int:
    try:
        _emit(run(build_parser().parse_args(argv)))
        return 0
    except (RegistryError, ValueError, OSError) as exc:
        _emit({"result": "rejected", "error": str(exc)}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
