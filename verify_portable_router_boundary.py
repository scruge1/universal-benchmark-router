#!/usr/bin/env python3
"""Verify that the universal router core is independent of local rig adapters."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Iterable


PORTABLE_SOURCES = (
    "universal_model_router.py",
    "universal_benchmark_exchange.py",
    "universal_benchmark_registry.py",
    "universal_task_intake.py",
    "universal_router_cli.py",
)
FORBIDDEN_IMPORT_ROOTS = {
    "dashboard",
    "docker",
    "gpu_lighting",
    "lifecycle",
    "model_control",
    "requests",
    "socket",
    "subprocess",
    "thermal",
    "urllib",
    "workload",
}
FORBIDDEN_HOST_MARKERS = (
    "/opt/rig-dashboard",
    "/run/rig-",
    "/var/lib/rig-dashboard",
    "100.84.3.33",
    "OpenRGB",
    "nvidia-smi",
    "rig-dashboard.service",
    "systemctl",
)


class BoundaryError(ValueError):
    """The portable core depends on a local adapter or host action."""


def imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def verify_sources(paths: Iterable[Path]) -> dict[str, object]:
    checked: list[str] = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        forbidden_imports = sorted(imported_roots(source) & FORBIDDEN_IMPORT_ROOTS)
        if forbidden_imports:
            raise BoundaryError(f"{path.name}: forbidden imports {forbidden_imports!r}")
        markers = sorted(marker for marker in FORBIDDEN_HOST_MARKERS if marker in source)
        if markers:
            raise BoundaryError(f"{path.name}: local host markers {markers!r}")
        checked.append(path.name)
    return {
        "schema": "portable-router-boundary-verification/v1",
        "result": "verified",
        "portable_sources": checked,
        "forbidden_adapter_imports": sorted(FORBIDDEN_IMPORT_ROOTS),
        "forbidden_host_markers": list(FORBIDDEN_HOST_MARKERS),
        "dashboard_is_consumer_only": True,
        "local_actuation_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    paths = [root / name for name in PORTABLE_SOURCES]
    print(json.dumps(verify_sources(paths), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
