"""Distribution identity and retained feature-module assembly."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

EDITION_MARKER = Path(__file__).resolve().parent.parent / "edition.json"
PERMISSION_MANAGEMENT = "permission_management"
DEFAULT_FEATURES = frozenset({PERMISSION_MANAGEMENT})


def _assembly() -> tuple[str, frozenset[str]]:
    requested = os.environ.get("SINKDUCE_EDITION", "").strip().lower()
    features = list(DEFAULT_FEATURES)
    if EDITION_MARKER.exists():
        marker = _read_json(EDITION_MARKER)
        if not isinstance(marker, dict) or marker.get("edition") not in ("community", "enterprise"):
            raise RuntimeError(f"invalid packaged edition marker: {EDITION_MARKER}")
        edition = marker["edition"]
        if requested and requested != edition:
            raise RuntimeError(f"packaged edition is fixed to {edition}")
        features = marker.get("features", features)
    else:
        edition = requested or "community"
    if edition not in ("community", "enterprise"):
        raise RuntimeError(f"unknown SINKDUCE_EDITION: {edition!r}")
    if not isinstance(features, list) or any(
        not isinstance(feature, str) or feature not in DEFAULT_FEATURES for feature in features
    ):
        raise RuntimeError(f"invalid packaged feature set: {EDITION_MARKER}")
    return edition, frozenset(features)


def has_permission_management() -> bool:
    """Full community and enterprise assemblies share the retained implementation."""
    _, features = _assembly()
    if PERMISSION_MANAGEMENT not in features:
        return False
    if importlib.util.find_spec("ee") is None:
        raise RuntimeError("permission management requested, but ee package is absent")
    return True


class IncompatibleEditionData(RuntimeError):
    """Stored permission data is invalid or unsupported by the assembled features."""


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IncompatibleEditionData(f"cannot read permission data: {path}") from exc


def mark_enterprise_used(data_dir: Path) -> None:
    """Persist a downgrade barrier after changing enterprise permissions."""
    from src.atomic_io import write_text_atomic

    path = data_dir / "identity" / "edition.json"
    write_text_atomic(path, '{"version": 1, "edition": "enterprise"}\n')


def assert_community_compatible(data_dir: Path) -> None:
    """Keep the restricted-core downgrade guard for existing migration tools."""
    _validate_permission_data(data_dir, allow_permissions=False)


def assert_data_compatible(data_dir: Path) -> None:
    """Validate stored permissions against the packaged feature set without writing."""
    _validate_permission_data(data_dir, allow_permissions=has_permission_management())


def _validate_permission_data(data_dir: Path, *, allow_permissions: bool) -> None:
    identity = data_dir / "identity"
    marker = identity / "edition.json"
    if marker.exists():
        raw = _read_json(marker)
        if not isinstance(raw, dict) or raw.get("edition") != "enterprise":
            raise IncompatibleEditionData(f"invalid permission marker: {marker}")
        if not allow_permissions:
            raise IncompatibleEditionData(f"enterprise permission marker present: {marker}")

    roles = identity / "roles.json"
    if roles.exists():
        raw = _read_json(roles)
        if not isinstance(raw, dict) or not isinstance(raw.get("roles"), list):
            raise IncompatibleEditionData(f"invalid role data: {roles}")
        if any(not isinstance(role, dict) for role in raw["roles"]):
            raise IncompatibleEditionData(f"invalid role data: {roles}")
        if raw["roles"] and not allow_permissions:
            raise IncompatibleEditionData(f"custom roles require enterprise edition: {roles}")

    domains = identity / "domain_acl.json"
    if domains.exists():
        raw = _read_json(domains)
        from src.identity import acl

        if not isinstance(raw, dict) or not isinstance(raw.get("domains"), dict):
            raise IncompatibleEditionData(f"invalid domain ACL data: {domains}")
        given = raw["domains"]
        for domain, tiers in given.items():
            if domain not in acl.DOMAIN_TIERS or not isinstance(tiers, dict):
                raise IncompatibleEditionData(f"unknown domain ACL data: {domains}")
            for tier, members in tiers.items():
                if tier not in acl.DOMAIN_TIERS[domain] and tier not in ("operate",):
                    raise IncompatibleEditionData(f"unknown domain ACL tier: {domains}")
                if not isinstance(members, list) or any(not isinstance(member, str) for member in members):
                    raise IncompatibleEditionData(f"invalid domain ACL tier: {domains}")
        if not allow_permissions and acl._normalize_domain_acl(raw) != acl.default_domain_acl():
            raise IncompatibleEditionData(f"custom domain ACL requires enterprise edition: {domains}")

    collections = data_dir / "collections"
    if collections.exists():
        for path in collections.glob("*/meta.json"):
            raw = _read_json(path)
            if not isinstance(raw, dict):
                raise IncompatibleEditionData(f"invalid collection metadata: {path}")
            if "acl" in raw:
                if not allow_permissions:
                    raise IncompatibleEditionData(f"collection ACL requires enterprise edition: {path}")
                from src.identity.acl import COLLECTION_TIERS

                value = raw["acl"]
                if not isinstance(value, dict) or any(
                    tier not in COLLECTION_TIERS
                    or not isinstance(members, list)
                    or any(not isinstance(member, str) for member in members)
                    for tier, members in value.items()
                ):
                    raise IncompatibleEditionData(f"invalid collection ACL: {path}")

    tokens = data_dir / "pairing" / "tokens.json"
    if tokens.exists():
        raw = _read_json(tokens)
        if not isinstance(raw, dict) or not isinstance(raw.get("tokens"), list):
            raise IncompatibleEditionData(f"invalid pairing token data: {tokens}")
        for item in raw["tokens"]:
            if not isinstance(item, dict):
                raise IncompatibleEditionData(f"invalid pairing token data: {tokens}")
            if not isinstance(item.get("role", "member"), str):
                raise IncompatibleEditionData(f"invalid pairing token role: {tokens}")
            if not allow_permissions and item.get("name") != "__local__" and not item.get("revoked_at") and item.get("role", "member") != "member":
                raise IncompatibleEditionData(f"non-Member device key requires enterprise edition: {tokens}")


def is_enterprise() -> bool:
    """Return distribution identity; capability checks use their own resolver."""
    edition, _ = _assembly()
    if edition == "community":
        return False
    if importlib.util.find_spec("ee") is None:
        raise RuntimeError("enterprise edition requested, but ee package is absent")
    return True
