"""SP-06: shared request() + domain split; original import paths stay."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "frontend" / "src" / "api"
FRONTEND_SRC = ROOT / "frontend" / "src"


def test_shared_http_helper_exists():
    http = (API / "http.ts").read_text(encoding="utf-8")
    assert "export async function request" in http
    assert 'flavor === "file-mgmt"' in http
    assert "export class FileMgmtApiError" in http
    assert "export function getNameConflict" in http
    assert "export const API_BASE" in http
    assert "export const FILE_MGMT_BASE" in http


def test_client_is_facade_and_reexports_domains():
    client = (API / "client.ts").read_text(encoding="utf-8")
    assert 'export * from "./collections"' in client
    assert 'export * from "./config"' in client
    assert 'export * from "./meeting"' in client
    assert 'export * from "./notes"' in client
    assert 'export * from "./chat"' in client
    assert 'from "./http"' in client
    assert "async function request" not in client


def test_domain_files_export_expected_entrypoints():
    meeting = (API / "meeting.ts").read_text(encoding="utf-8")
    notes = (API / "notes.ts").read_text(encoding="utf-8")
    chat = (API / "chat.ts").read_text(encoding="utf-8")
    config = (API / "config.ts").read_text(encoding="utf-8")
    assert "export const getMeetings" in meeting
    assert "export function streamBlueprint" in meeting
    assert "export const getNotes" in notes
    assert "export async function listSessions" in chat
    assert "export const confirmWebSearch" in chat
    assert "export const getConfig" in config
    assert "export const getLLMProviders" in config


def test_file_mgmt_uses_shared_request_and_keeps_error_exports():
    fm = (API / "file-mgmt.ts").read_text(encoding="utf-8")
    assert 'from "./http"' in fm
    assert 'flavor: "file-mgmt"' in fm
    assert "export { FileMgmtApiError, getNameConflict" in fm
    assert "export class FileMgmtApiError" not in fm
    assert "export const getFolderTree" in fm


def test_components_still_import_original_api_entrypoints():
    """Domain modules exist, but UI must not switch import paths in this slice."""
    banned = (
        'from "@/api/meeting"',
        'from "@/api/notes"',
        'from "@/api/chat"',
        'from "@/api/config"',
        'from "@/api/collections"',
        'from "@/api/http"',
        'from "@/api/recall"',
        'from "@/api/hot-words"',
    )
    hits: list[str] = []
    for path in FRONTEND_SRC.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if API in path.parents or path.parent == API:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            if needle in text:
                hits.append(f"{path.relative_to(ROOT)}: {needle}")
    assert hits == []
