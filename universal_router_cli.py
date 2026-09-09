"""Contributor command line for offline universal benchmark evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from universal_benchmark_exchange import (
    verify_acknowledgement,
    verify_benchmark_request,
    verify_contribution,
    verify_result_envelope,
)
from universal_benchmark_registry import FileRegistry, RegistryError, sha256_bytes, strict_json_bytes


def _load(path: Path) -> tuple[Any, str]:
    value = path.read_bytes()
    return strict_json_bytes(value), sha256_bytes(value)


def _emit(value: Any, *, stream: Any = sys.stdout) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")), file=stream)


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
