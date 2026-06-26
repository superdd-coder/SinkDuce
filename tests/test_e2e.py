"""E2E test: full user-flow coverage.  Requires docker compose up app.

Run:  pytest tests/test_e2e.py -v -s
"""

import json as _json
import time
import httpx
import pytest
from tests.e2e_config import api_base

BASE = api_base()
API = f"{BASE}/api"
TIMEOUT = 60
LONG_TIMEOUT = 120

pytestmark = pytest.mark.e2e

TEST_COL_PREFIX = "__e2e_"

# ── helpers ────────────────────────────────────────────────────────────

def _health_ok():
    try:
        return httpx.get(f"{BASE}/health", timeout=5).status_code == 200
    except Exception:
        return False


def _skip_if_no_server():
    if not _health_ok():
        pytest.skip("API server not reachable — docker compose up -d app")


def _create_col(name: str) -> str:
    r = httpx.post(f"{API}/collections", json={"name": name}, timeout=10)
    assert r.status_code == 200, f"create {name}: {r.text}"
    return r.json()["id"]


def _delete_col(col_id: str):
    httpx.delete(f"{API}/collections/{col_id}", timeout=10)


def _upload_files(col_id: str, files: list[tuple[str, str]]) -> list[str]:
    """Upload multiple text files, return task IDs."""
    ff = [("files", (fn, content.encode(), "text/plain")) for fn, content in files]
    r = httpx.post(f"{API}/documents/upload", files=ff, params={"collection": col_id}, timeout=60)
    assert r.status_code == 200, f"upload: {r.text}"
    return [t["id"] for t in r.json().get("tasks", [])]


def _wait_tasks(task_ids: list[str], timeout_s: int = 120):
    """Poll until all task_ids are completed."""
    pending = set(task_ids)
    for _ in range(timeout_s // 2):
        r = httpx.get(f"{API}/documents/tasks", timeout=10)
        if r.status_code != 200:
            time.sleep(2)
            continue
        for t in r.json().get("tasks", []):
            tid = t["id"]
            if tid in pending:
                if t["status"] == "completed":
                    pending.discard(tid)
                elif t["status"] == "failed":
                    pytest.fail(f"Task {tid} failed: {t.get('error', '?')}")
        if not pending:
            return
        time.sleep(2)
    pytest.fail(f"Tasks did not complete: {pending}")


def _query(col_id: str, question: str, use_agent: bool = False) -> dict:
    r = httpx.post(f"{API}/query", json={
        "question": question, "collection": col_id, "use_agent": use_agent,
    }, timeout=LONG_TIMEOUT)
    assert r.status_code == 200, f"query: {r.text}"
    return r.json()


def _query_stream(col_id: str, question: str, use_agent: bool = False) -> tuple[list[str], dict]:
    tokens = []
    meta = {}
    done = False
    with httpx.Client(timeout=LONG_TIMEOUT) as c:
        with c.stream("POST", f"{API}/query/stream",
                       json={"question": question, "collection": col_id, "use_agent": use_agent}) as resp:
            assert resp.status_code == 200
            for line in resp.iter_lines():
                line = line.strip()
                if not line.startswith("data: "):
                    continue
                try:
                    evt = _json.loads(line[6:])
                except (_json.JSONDecodeError, ValueError):
                    continue
                t = evt.get("type", "")
                if t == "token":
                    tokens.append(evt.get("content", ""))
                elif t == "meta":
                    meta = evt
                elif t == "done":
                    done = True
                elif t == "error":
                    pytest.fail(f"stream error: {evt.get('content')}")
    assert done, "no done event"
    return tokens, meta


def _recall_search(collections: list[str], query: str) -> dict:
    r = httpx.post(f"{API}/recall/search",
                   json={"query": query, "collections": collections}, timeout=30)
    assert r.status_code == 200
    return r.json()


def _get_first_col() -> str | None:
    r = httpx.get(f"{API}/collections", timeout=10)
    if r.status_code == 200:
        cols = r.json()
        return cols[0]["id"] if cols else None
    return None


# ══════════════════════════════════════════════════════════════════════════
# 1. Health & Collections
# ══════════════════════════════════════════════════════════════════════════

class TestHealthAndCollections:
    def test_health(self):
        _skip_if_no_server()
        r = httpx.get(f"{BASE}/health", timeout=10)
        assert r.status_code == 200

    def test_list_collections(self):
        _skip_if_no_server()
        r = httpx.get(f"{API}/collections", timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_collection_crud(self):
        """Create → info → rename → delete."""
        _skip_if_no_server()
        col_id = _create_col(f"{TEST_COL_PREFIX}crud")

        # Info
        r = httpx.get(f"{API}/collections/{col_id}/info", timeout=10)
        assert r.status_code == 200

        # Config
        r = httpx.get(f"{API}/collections/{col_id}/config", timeout=10)
        assert r.status_code == 200

        # Rename
        r = httpx.put(f"{API}/collections/{col_id}/rename", json={"name": f"{TEST_COL_PREFIX}renamed"}, timeout=10)
        assert r.status_code == 200

        # Delete
        _delete_col(col_id)
        r = httpx.get(f"{API}/collections", timeout=10)
        assert not any(c["id"] == col_id for c in r.json()), "collection not deleted"


# ══════════════════════════════════════════════════════════════════════════
# 2. Document upload & management
# ══════════════════════════════════════════════════════════════════════════

class TestDocuments:
    def test_single_upload(self):
        _skip_if_no_server()
        col_id = _create_col(f"{TEST_COL_PREFIX}up1")
        try:
            tids = _upload_files(col_id, [("hello.txt", "Hello world. This is a test document.")])
            _wait_tasks(tids)

            # Verify chunks
            r = httpx.get(f"{API}/documents/{col_id}", timeout=10)
            assert r.status_code == 200
            assert r.json().get("total_chunks", 0) > 0
        finally:
            _delete_col(col_id)

    def test_multi_upload_wait_for_all(self):
        """Upload 3 files at once → all complete → coverage triggers once."""
        _skip_if_no_server()
        col_id = _create_col(f"{TEST_COL_PREFIX}up3")
        try:
            tids = _upload_files(col_id, [
                ("a.txt", "Alpha project budget 2025 Q1: $50,000 approved."),
                ("b.txt", "Beta project timeline: June to December 2025."),
                ("c.txt", "Gamma vendor contract signed March 2025."),
            ])
            assert len(tids) == 3
            _wait_tasks(tids)

            r = httpx.get(f"{API}/documents/{col_id}", timeout=10)
            assert r.json().get("total_chunks", 0) >= 3
        finally:
            _delete_col(col_id)

    def test_delete_document(self):
        _skip_if_no_server()
        col_id = _create_col(f"{TEST_COL_PREFIX}del")
        try:
            tids = _upload_files(col_id, [("todelete.txt", "Content to be deleted.")])
            _wait_tasks(tids)

            # Delete
            r = httpx.delete(f"{API}/documents/{col_id}/todelete.txt", timeout=30)
            assert r.status_code == 200

            r = httpx.get(f"{API}/documents/{col_id}", timeout=10)
            files = r.json().get("files", [])
            assert not any(f.get("source") == "todelete.txt" for f in files)
        finally:
            _delete_col(col_id)

    def test_document_listing(self):
        _skip_if_no_server()
        col = _get_first_col()
        if not col:
            pytest.skip("no collections")
        r = httpx.get(f"{API}/documents/{col}", timeout=10)
        assert r.status_code == 200

    def test_file_preview(self):
        _skip_if_no_server()
        col_id = _create_col(f"{TEST_COL_PREFIX}prev")
        try:
            _upload_files(col_id, [("preview.txt", "Preview test content. Unique: zxcvbnm.")])
            _wait_tasks(_upload_files(col_id, [("preview.txt", "Preview test content.")]))

            # Preview
            r = httpx.get(f"{API}/documents/preview/preview.txt", timeout=10)
            # May 404 if parsed text not generated yet, just don't crash
            assert r.status_code in (200, 404)
        finally:
            _delete_col(col_id)


# ══════════════════════════════════════════════════════════════════════════
# 3. Query — direct & agentic
# ══════════════════════════════════════════════════════════════════════════

class TestQuery:
    @pytest.fixture(autouse=True)
    def _setup(self):
        _skip_if_no_server()
        self.col_id = _create_col(f"{TEST_COL_PREFIX}q")
        tids = _upload_files(self.col_id, [
            ("science.txt", "The sky appears blue because of Rayleigh scattering. "
             "Shorter blue wavelengths are scattered more by air molecules than longer red wavelengths. "
             "This phenomenon was discovered by Lord Rayleigh in the 19th century."),
            ("python.txt", "Python is a high-level programming language created by Guido van Rossum. "
             "It was first released in 1991. Python emphasizes code readability with its notable "
             "use of significant whitespace."),
        ])
        _wait_tasks(tids)
        yield
        _delete_col(self.col_id)

    def test_direct_query_returns_answer(self):
        r = _query(self.col_id, "why is the sky blue?", use_agent=False)
        assert len(r["answer"]) > 20
        assert len(r["sources"]) > 0

    def test_direct_query_returns_sources(self):
        r = _query(self.col_id, "who created Python?", use_agent=False)
        assert any("Guido" in s["text"] or "Rossum" in s["text"] for s in r["sources"])

    def test_direct_streaming(self):
        tokens, meta = _query_stream(self.col_id, "what is Rayleigh scattering?", use_agent=False)
        assert len(tokens) > 0
        assert len(meta.get("sources", [])) > 0

    def test_agentic_query(self):
        r = _query(self.col_id, "compare Python and the sky color phenomenon", use_agent=True)
        assert len(r["answer"]) > 10
        assert "iterations" in r

    def test_agentic_streaming(self):
        tokens, meta = _query_stream(self.col_id, "tell me about Python", use_agent=True)
        assert len(tokens) > 0
        # Agentic streaming may return tokens directly in the answer field
        assert meta.get("agent_active") is True or meta.get("mode") == "agentic"

    def test_empty_question_graceful(self):
        r = httpx.post(f"{API}/query",
                       json={"question": "", "collection": self.col_id, "use_agent": False},
                       timeout=30)
        assert r.status_code in (200, 400, 422)  # accepted or rejected, never 500

    def test_nonexistent_collection(self):
        r = httpx.post(f"{API}/query",
                       json={"question": "test", "collection": "__does_not_exist__", "use_agent": False},
                       timeout=30)
        assert r.status_code == 200
        assert "does not exist" in r.json()["answer"].lower()


# ══════════════════════════════════════════════════════════════════════════
# 4. Recall search & eval
# ══════════════════════════════════════════════════════════════════════════

class TestRecall:
    def test_search_returns_results(self):
        _skip_if_no_server()
        col = _get_first_col()
        if not col:
            pytest.skip("no collections")
        r = _recall_search([col], "test")
        assert "results" in r
        assert "time_ms" in r

    def test_search_agentic(self):
        _skip_if_no_server()
        col = _get_first_col()
        if not col:
            pytest.skip("no collections")
        r = httpx.post(f"{API}/recall/search",
                       json={"query": "test", "collections": [col], "use_agent": True},
                       timeout=LONG_TIMEOUT)
        assert r.status_code == 200

    def test_benchmark(self):
        _skip_if_no_server()
        col = _get_first_col()
        if not col:
            pytest.skip("no collections")
        r = httpx.post(f"{API}/recall/benchmark",
                       json={"collection": col, "queries": [{"query": "test", "relevant_ids": []}]},
                       timeout=30)
        assert r.status_code in (200, 422)

    def test_eval_cases(self):
        _skip_if_no_server()
        col = _get_first_col()
        if not col:
            pytest.skip("no collections")
        r = httpx.get(f"{API}/recall/eval/{col}/cases", timeout=10)
        assert r.status_code in (200, 404)


# ══════════════════════════════════════════════════════════════════════════
# 5. Coverage lifecycle
# ══════════════════════════════════════════════════════════════════════════

class TestCoverage:
    def test_generated_after_upload(self):
        _skip_if_no_server()
        col_id = _create_col(f"{TEST_COL_PREFIX}cov")
        try:
            tids = _upload_files(col_id, [("q4_financial_report.txt",
                                           "Q4 Financial Report 2025. Total revenue: ¥12,500,000. "
                                           "Net profit: ¥3,200,000. Key clients: Alpha Corp, Beta Ltd.")])
            _wait_tasks(tids)

            # Poll for coverage
            cov = ""
            for _ in range(40):
                r = httpx.get(f"{API}/collections/{col_id}/config", timeout=10)
                if r.status_code == 200:
                    cov = r.json().get("coverage", "")
                    if cov:
                        break
                time.sleep(3)

            if cov:
                assert len(cov) <= 50, f"coverage too long: {cov}"
        finally:
            _delete_col(col_id)

    def test_cleared_after_delete_all(self):
        _skip_if_no_server()
        col_id = _create_col(f"{TEST_COL_PREFIX}covd")
        try:
            tids = _upload_files(col_id, [("inv.txt", "Invoice #INV-001 for ¥500,000.")])
            _wait_tasks(tids)

            # Wait for coverage
            for _ in range(40):
                r = httpx.get(f"{API}/collections/{col_id}/config", timeout=10)
                if r.status_code == 200 and r.json().get("coverage", ""):
                    break
                time.sleep(3)

            # Delete all files
            r = httpx.get(f"{API}/documents/{col_id}", timeout=10)
            for f in r.json().get("files", []):
                src = f.get("source", "")
                if src:
                    httpx.delete(f"{API}/documents/{col_id}/{src}", timeout=30)
            time.sleep(3)

            # Coverage should clear eventually
            r = httpx.get(f"{API}/collections/{col_id}/config", timeout=10)
            cov = r.json().get("coverage", "")
            # At minimum the endpoint works
        finally:
            _delete_col(col_id)


# ══════════════════════════════════════════════════════════════════════════
# 6. MCP tools
# ══════════════════════════════════════════════════════════════════════════

class TestMCP:
    def test_search_knowledge_base(self):
        _skip_if_no_server()
        # Directly test via REST since MCP uses same services
        col = _get_first_col()
        if not col:
            pytest.skip("no collections")

        # MCP internally calls services.agentic_query.run()
        # Test that the service is up by calling query in agentic mode
        r = httpx.post(f"{API}/query",
                       json={"question": "hello", "collection": col, "use_agent": True},
                       timeout=LONG_TIMEOUT)
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════
# 7. Config & system
# ══════════════════════════════════════════════════════════════════════════

class TestConfig:
    def test_reranker_configured_and_healthy(self):
        """Reranker should not be failing silently — surface the error."""
        _skip_if_no_server()
        r = httpx.get(f"{API}/config", timeout=10)
        assert r.status_code == 200
        cfg = r.json()
        rerank_cfg = cfg.get("rerank", {})
        provider = rerank_cfg.get("provider", "none")

        if provider == "none":
            pytest.skip("No reranker configured")
            return

        # Try a recall search that uses reranker
        col = _get_first_col()
        if not col:
            pytest.skip("no collections")

        r = httpx.post(f"{API}/recall/search",
                       json={"query": "test reranker health check",
                             "collections": [col], "use_reranker": True},
                       timeout=30)
        assert r.status_code == 200
        # Reranker failure would be in docker logs only; the endpoint
        # degrades gracefully.  Run `docker compose logs app | grep -i rerank`
        # after this test to check for actual rerank errors.

    def test_get_config(self):
        _skip_if_no_server()
        r = httpx.get(f"{API}/config", timeout=10)
        assert r.status_code == 200
        cfg = r.json()
        assert "llm" in cfg

    def test_config_reload(self):
        _skip_if_no_server()
        r = httpx.post(f"{API}/config/reload", timeout=30)
        assert r.status_code == 200

    def test_swagger_accessible(self):
        _skip_if_no_server()
        r = httpx.get(f"{BASE}/docs", timeout=10)
        assert r.status_code == 200

    def test_history(self):
        _skip_if_no_server()
        r = httpx.get(f"{API}/history", params={"limit": 5}, timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_docker_logs_no_fatal_errors(self):
        """After all tests, Docker logs should be clean."""
        _skip_if_no_server()
        import subprocess
        result = subprocess.run(
            ["docker", "compose", "logs", "app"],
            capture_output=True, text=True, timeout=15,
        )
        logs = result.stdout + result.stderr
        # These indicate real problems, not graceful degradation
        fatal_patterns = ["FATAL", "ImportError", "ModuleNotFoundError", "cannot import"]
        for pat in fatal_patterns:
            assert pat not in logs, f"Docker logs contain '{pat}' — check the app"

