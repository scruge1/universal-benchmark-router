"""Offline append-only custody for verified universal benchmark evidence."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from universal_benchmark_exchange import (
    ACK_SCHEMA,
    CONTRIBUTION_SCHEMA,
    REQUEST_SCHEMA,
    RESULT_ENVELOPE_SCHEMA,
    verify_acknowledgement,
    verify_benchmark_request,
    verify_contribution,
    verify_result_envelope,
)
from universal_model_router import canonical_sha256


REGISTRY_SCHEMA = "universal-benchmark-registry/v1"
EVENT_SCHEMA = "universal-benchmark-registry-event/v1"
CLAIM_SCHEMA = "universal-benchmark-registry-claim/v1"
REGISTRY_VERSION = "h7-p2-rg1-v1"


class RegistryError(ValueError):
    """A registry operation is unsafe, incomplete, replayed, or conflicting."""


class RegistryReplayError(RegistryError):
    """The exact document identity has already been accepted."""


class RegistryConflictError(RegistryError):
    """A document identity is already bound to different bytes."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def strict_json_bytes(value: bytes) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise RegistryError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    try:
        return json.loads(value.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryError("strict UTF-8 JSON is required") from exc


def document_identity(document: Mapping[str, Any]) -> tuple[str, str]:
    schema = document.get("schema")
    mapping = {
        REQUEST_SCHEMA: ("request", "request_id"),
        RESULT_ENVELOPE_SCHEMA: ("result", "result_id"),
        CONTRIBUTION_SCHEMA: ("contribution", "submission_id"),
        ACK_SCHEMA: ("acknowledgement", "acknowledgement_id"),
    }
    definition = mapping.get(schema)
    if definition is None:
        raise RegistryError("unsupported document schema")
    document_type, field = definition
    identity = document.get(field)
    if not isinstance(identity, str) or not identity:
        raise RegistryError(f"{field}: non-empty identity is required")
    return document_type, identity


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _require_sha256(value: Any, path: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise RegistryError(f"{path}: lowercase SHA-256 is required")


def _require_timestamp(value: Any, path: str) -> None:
    if not isinstance(value, str):
        raise RegistryError(f"{path}: timezone-aware timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistryError(f"{path}: invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise RegistryError(f"{path}: timezone-aware timestamp is required")


class FileRegistry:
    """An initialized, offline registry with content-addressed immutable records."""

    def __init__(self, root: Path | str) -> None:
        requested_path = Path(root)
        if requested_path.exists():
            self._reject_link(requested_path)
        self.root = requested_path.resolve()
        marker_path = self.root / "registry.json"
        if not marker_path.is_file() or marker_path.is_symlink():
            raise RegistryError("initialized registry marker is required")
        marker_bytes = marker_path.read_bytes()
        marker = strict_json_bytes(marker_bytes)
        expected = {
            "schema": REGISTRY_SCHEMA,
            "hash_algorithm": "sha256",
            "storage": "canonical_json_content_addressed_absent_only",
            "authority": "offline_saved_evidence_only",
        }
        if marker != expected or marker_bytes != canonical_json_bytes(expected):
            raise RegistryError("registry marker drift")
        self._reject_link(self.root)

    @classmethod
    def initialize(cls, root: Path | str) -> "FileRegistry":
        requested_path = Path(root)
        if requested_path.exists():
            cls._reject_link(requested_path)
        path = requested_path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        cls._reject_link(path)
        marker = {
            "schema": REGISTRY_SCHEMA,
            "hash_algorithm": "sha256",
            "storage": "canonical_json_content_addressed_absent_only",
            "authority": "offline_saved_evidence_only",
        }
        cls._exclusive_write(path / "registry.json", canonical_json_bytes(marker), path)
        for child in ("objects/sha256", "events/sha256", "claims/sha256"):
            (path / child).mkdir(parents=True, exist_ok=True)
        return cls(path)

    @staticmethod
    def _reject_link(path: Path) -> None:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise RegistryError(f"links are forbidden in registry paths: {path}")

    @classmethod
    def _safe_parent(cls, path: Path, root: Path) -> None:
        resolved_root = root.resolve(strict=True)
        resolved_parent = path.parent.resolve(strict=True)
        if resolved_parent != resolved_root and resolved_root not in resolved_parent.parents:
            raise RegistryError("registry path escape")
        cursor = resolved_parent
        while True:
            cls._reject_link(cursor)
            if cursor == resolved_root:
                break
            cursor = cursor.parent

    @classmethod
    def _exclusive_write(cls, path: Path, value: bytes, root: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        cls._safe_parent(path, root)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        try:
            descriptor = os.open(path, flags, 0o444)
        except FileExistsError as exc:
            raise RegistryReplayError(f"absent-only target already exists: {path.name}") from exc
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                path.unlink(missing_ok=True)
            finally:
                raise

    @classmethod
    def _ensure_content(cls, path: Path, value: bytes, root: Path) -> None:
        """Create immutable bytes, or accept exact orphan bytes from an interrupted peer."""
        if path.exists():
            cls._reject_link(path)
            if path.read_bytes() != value:
                raise RegistryConflictError(f"content-addressed path has different bytes: {path.name}")
            return
        try:
            cls._exclusive_write(path, value, root)
        except RegistryReplayError:
            cls._reject_link(path)
            if path.read_bytes() != value:
                raise RegistryConflictError(f"content-addressed path has different bytes: {path.name}") from None

    def _hash_path(self, area: str, digest: str) -> Path:
        _require_sha256(digest, "$digest")
        path = self.root / area / "sha256" / digest[:2] / f"{digest[2:]}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._safe_parent(path, self.root)
        return path

    def _claim_path(self, document_type: str, identity: str) -> Path:
        claim_key = canonical_sha256({"document_type": document_type, "identity": identity})
        return self._hash_path("claims", claim_key)

    def _existing_claim(self, document_type: str, identity: str) -> Mapping[str, Any] | None:
        path = self._claim_path(document_type, identity)
        if not path.exists():
            return None
        self._reject_link(path)
        claim = strict_json_bytes(path.read_bytes())
        if not isinstance(claim, Mapping):
            raise RegistryError("claim must be an object")
        return claim

    def _require_accepted(self, document_type: str, identity: str, object_sha256: str) -> None:
        claim = self._existing_claim(document_type, identity)
        if claim is None:
            raise RegistryError(f"accepted {document_type} dependency is required: {identity}")
        if claim.get("object_sha256") != object_sha256:
            raise RegistryConflictError(f"accepted {document_type} dependency bytes differ: {identity}")

    def _store(
        self,
        document: Mapping[str, Any],
        dependencies: Mapping[str, str],
        *,
        source_sha256: str | None = None,
        accepted_at: str | None = None,
    ) -> dict[str, Any]:
        document_type, identity = document_identity(document)
        object_bytes = canonical_json_bytes(document)
        object_sha256 = sha256_bytes(object_bytes)
        if object_sha256 != canonical_sha256(document):
            raise RegistryError("canonical hash implementation drift")
        existing = self._existing_claim(document_type, identity)
        if existing is not None:
            if existing.get("object_sha256") == object_sha256:
                raise RegistryReplayError(f"replayed {document_type} identity: {identity}")
            raise RegistryConflictError(f"conflicting {document_type} identity: {identity}")
        for name, digest in dependencies.items():
            _require_sha256(digest, f"dependency {name}")
        if source_sha256 is not None:
            _require_sha256(source_sha256, "$source_sha256")
        event_time = accepted_at or _utc_now()
        _require_timestamp(event_time, "$accepted_at")
        event = {
            "schema": EVENT_SCHEMA,
            "accepted_at": event_time,
            "validator_version": REGISTRY_VERSION,
            "document_type": document_type,
            "identity": identity,
            "object_sha256": object_sha256,
            "source_sha256": source_sha256 or object_sha256,
            "dependencies": dict(sorted(dependencies.items())),
            "authority": "offline_saved_evidence_only",
        }
        event_bytes = canonical_json_bytes(event)
        event_sha256 = sha256_bytes(event_bytes)
        claim = {
            "schema": CLAIM_SCHEMA,
            "document_type": document_type,
            "identity": identity,
            "object_sha256": object_sha256,
            "event_sha256": event_sha256,
            "authority": "immutable_identity_binding_only",
        }
        object_path = self._hash_path("objects", object_sha256)
        event_path = self._hash_path("events", event_sha256)
        self._ensure_content(object_path, object_bytes, self.root)
        self._ensure_content(event_path, event_bytes, self.root)
        try:
            self._exclusive_write(self._claim_path(document_type, identity), canonical_json_bytes(claim), self.root)
        except (RegistryReplayError, RegistryConflictError):
            existing = self._existing_claim(document_type, identity)
            if existing and existing.get("object_sha256") == object_sha256:
                raise RegistryReplayError(f"replayed {document_type} identity: {identity}") from None
            raise RegistryConflictError(f"conflicting {document_type} identity: {identity}") from None
        return claim

    def ingest_request(
        self,
        request: Mapping[str, Any],
        capability_contract: Mapping[str, Any],
        public_keys: Mapping[str, str],
        **metadata: Any,
    ) -> dict[str, Any]:
        verify_benchmark_request(request, capability_contract, public_keys)
        return self._store(
            request,
            {"capability_contract_sha256": canonical_sha256(capability_contract)},
            **metadata,
        )

    def ingest_contribution(
        self,
        contribution: Mapping[str, Any],
        suite: Mapping[str, Any],
        public_keys: Mapping[str, str],
        **metadata: Any,
    ) -> dict[str, Any]:
        verify_contribution(contribution, suite, public_keys)
        return self._store(contribution, {"suite_sha256": canonical_sha256(suite)}, **metadata)

    def ingest_result(
        self,
        result: Mapping[str, Any],
        request: Mapping[str, Any],
        capability_contract: Mapping[str, Any],
        suite: Mapping[str, Any],
        public_keys: Mapping[str, str],
        **metadata: Any,
    ) -> dict[str, Any]:
        verify_result_envelope(result, request, capability_contract, suite, public_keys)
        contribution = result["contribution"]
        self._require_accepted("request", request["request_id"], canonical_sha256(request))
        self._require_accepted(
            "contribution", contribution["submission_id"], canonical_sha256(contribution)
        )
        return self._store(
            result,
            {
                "capability_contract_sha256": canonical_sha256(capability_contract),
                "contribution_sha256": canonical_sha256(contribution),
                "request_sha256": canonical_sha256(request),
                "suite_sha256": canonical_sha256(suite),
            },
            **metadata,
        )

    def ingest_acknowledgement(
        self,
        acknowledgement: Mapping[str, Any],
        contribution: Mapping[str, Any],
        suite: Mapping[str, Any],
        public_keys: Mapping[str, str],
        **metadata: Any,
    ) -> dict[str, Any]:
        verify_acknowledgement(acknowledgement, contribution, suite, public_keys)
        self._require_accepted(
            "contribution", contribution["submission_id"], canonical_sha256(contribution)
        )
        return self._store(
            acknowledgement,
            {
                "contribution_sha256": canonical_sha256(contribution),
                "suite_sha256": canonical_sha256(suite),
            },
            **metadata,
        )

    def read(self, document_type: str, identity: str) -> Any:
        claim = self._existing_claim(document_type, identity)
        if claim is None:
            raise RegistryError(f"unknown {document_type} identity: {identity}")
        path = self._hash_path("objects", claim["object_sha256"])
        value = path.read_bytes()
        if sha256_bytes(value) != claim["object_sha256"]:
            raise RegistryError("object integrity failure")
        return strict_json_bytes(value)

    def audit(self) -> dict[str, Any]:
        claim_root = self.root / "claims" / "sha256"
        claims = sorted(claim_root.glob("*/*.json"))
        object_paths = sorted((self.root / "objects" / "sha256").glob("*/*.json"))
        event_paths = sorted((self.root / "events" / "sha256").glob("*/*.json"))
        for path in object_paths + event_paths:
            self._reject_link(path)
            digest = path.parent.name + path.stem
            if sha256_bytes(path.read_bytes()) != digest:
                raise RegistryError(f"content-addressed integrity failure: {path.name}")
        identities: set[tuple[str, str]] = set()
        for path in claims:
            self._reject_link(path)
            claim_bytes = path.read_bytes()
            claim = strict_json_bytes(claim_bytes)
            expected_claim_keys = {
                "schema", "document_type", "identity", "object_sha256", "event_sha256", "authority"
            }
            if (
                not isinstance(claim, Mapping)
                or set(claim) != expected_claim_keys
                or claim.get("schema") != CLAIM_SCHEMA
                or claim.get("authority") != "immutable_identity_binding_only"
                or claim_bytes != canonical_json_bytes(claim)
            ):
                raise RegistryError(f"invalid claim: {path.name}")
            document_type = claim.get("document_type")
            identity = claim.get("identity")
            key = (document_type, identity)
            if key in identities:
                raise RegistryError("duplicate identity claims")
            identities.add(key)
            expected_key = canonical_sha256({"document_type": document_type, "identity": identity})
            if path.parent.name + path.stem != expected_key:
                raise RegistryError("claim path identity drift")
            object_bytes = self._hash_path("objects", claim["object_sha256"]).read_bytes()
            if sha256_bytes(object_bytes) != claim["object_sha256"]:
                raise RegistryError("object integrity failure")
            document = strict_json_bytes(object_bytes)
            if document_identity(document) != key:
                raise RegistryError("object identity drift")
            event_bytes = self._hash_path("events", claim["event_sha256"]).read_bytes()
            if sha256_bytes(event_bytes) != claim["event_sha256"]:
                raise RegistryError("event integrity failure")
            event = strict_json_bytes(event_bytes)
            expected_event_keys = {
                "schema", "accepted_at", "validator_version", "document_type", "identity",
                "object_sha256", "source_sha256", "dependencies", "authority",
            }
            if (
                not isinstance(event, Mapping)
                or set(event) != expected_event_keys
                or event.get("schema") != EVENT_SCHEMA
                or event.get("validator_version") != REGISTRY_VERSION
                or event.get("authority") != "offline_saved_evidence_only"
                or event_bytes != canonical_json_bytes(event)
            ):
                raise RegistryError("invalid acceptance event")
            _require_timestamp(event["accepted_at"], "$event.accepted_at")
            _require_sha256(event["source_sha256"], "$event.source_sha256")
            if not isinstance(event["dependencies"], Mapping):
                raise RegistryError("event dependencies must be an object")
            for name, digest in event["dependencies"].items():
                _require_sha256(digest, f"$event.dependencies.{name}")
            for field in ("document_type", "identity", "object_sha256"):
                if event.get(field) != claim.get(field):
                    raise RegistryError(f"event {field} drift")
        return {
            "schema": "universal-benchmark-registry-audit/v1",
            "result": "verified",
            "accepted_identity_count": len(claims),
            "object_count": len(object_paths),
            "event_count": len(event_paths),
            "authority": "offline_integrity_verification_only",
        }
