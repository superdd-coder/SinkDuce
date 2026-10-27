"""Backend API integration test script.

Run: python3 tests/test_api.py
Requires: API server running (docker compose up app).
Port is read from .env, SINKDUCE_API_PORT env var, or defaults to 18900.
"""

import os
import sys
import httpx

def _read_port():
    env = os.environ.get("SINKDUCE_API_PORT")
    if env:
        return env
    try:
        with open(os.path.join(os.path.dirname(__file__), "..", ".env")) as f:
            for line in f:
                if line.strip().startswith("API_PORT="):
                    return line.strip().split("=", 1)[1].strip()
    except (OSError, IOError):
        pass
    return "18900"


TEST_PORT = _read_port()
BASE = f"http://127.0.0.1:{TEST_PORT}/api"
TIMEOUT = 30
PASS = 0
FAIL = 0


def ok(name: str):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}")


def fail(name: str, detail: str = ""):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name}: {detail}")


def test(label: str):
    """Simple context manager for grouped tests."""

    class _Ctx:
        def __enter__(self):
            print(f"\n{label}")
            return self

        def __exit__(self, *a):
            pass

    return _Ctx()


# ── Health ───────────────────────────────────────────

def test_health():
    with test("Health"):
        r = httpx.get(f"http://127.0.0.1:{TEST_PORT}/health", timeout=TIMEOUT)
        if r.status_code == 200 and r.json().get("status") == "ok":
            ok("GET /health")
        else:
            fail("GET /health", f"status={r.status_code}")


# ── Collections ──────────────────────────────────────

def test_collections():
    test_col = "__test_collection__"
    col_id = None

    with test("Collections"):
        # List
        r = httpx.get(f"{BASE}/collections", timeout=TIMEOUT)
        if r.status_code == 200 and isinstance(r.json(), list):
            ok("GET /collections")
        else:
            fail("GET /collections", str(r.status_code))
            return

        # Create
        r = httpx.post(f"{BASE}/collections", json={"name": test_col}, timeout=TIMEOUT)
        if r.status_code == 200 and "id" in r.json():
            col_id = r.json()["id"]
            ok("POST /collections (create)")
        else:
            fail("POST /collections", r.text[:100])
            return

        # List (should include new)
        r = httpx.get(f"{BASE}/collections", timeout=TIMEOUT)
        found = any(c["id"] == col_id and c["name"] == test_col for c in r.json())
        if found:
            ok("Collection appears in list")
        else:
            fail("Collection appears in list", r.text[:100])

        # Info (using ID)
        r = httpx.get(f"{BASE}/collections/{col_id}/info", timeout=TIMEOUT)
        if r.status_code == 200 and r.json().get("name") == col_id:
            ok("GET /collections/{id}/info")
        else:
            fail("GET /collections/{id}/info", r.text[:100])

        # Delete (using ID)
        r = httpx.delete(f"{BASE}/collections/{col_id}", timeout=TIMEOUT)
        if r.status_code == 200 and "message" in r.json():
            ok("DELETE /collections/{id}")
        else:
            fail("DELETE /collections/{id}", r.text[:100])

        # Verify deleted
        r = httpx.get(f"{BASE}/collections", timeout=TIMEOUT)
        found = any(c["id"] == col_id for c in r.json())
        if not found:
            ok("Collection removed from list")
        else:
            fail("Collection removed from list")


# ── Documents ────────────────────────────────────────

def test_documents():
    with test("Documents"):
        # Check chunk count on default collection
        r = httpx.get(f"{BASE}/documents/default", timeout=TIMEOUT)
        if r.status_code == 200 and "total_chunks" in r.json():
            ok(f"GET /documents/default ({r.json()['total_chunks']} chunks)")
        else:
            fail("GET /documents/default", r.text[:100])

        # Check non-existent collection (should not 500)
        r = httpx.get(f"{BASE}/documents/__nonexistent__", timeout=TIMEOUT)
        if r.status_code == 200:
            ok("GET /documents/{nonexistent} (graceful)")
        else:
            fail("GET /documents/{nonexistent}", f"status={r.status_code}")


# ── Upload ───────────────────────────────────────────

def test_upload():
    with test("Upload"):
        # Create a temp file and upload
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w")
        tmp.write("This is a test document for API testing. It contains some sample text.")
        tmp.close()

        try:
            with open(tmp.name, "rb") as f:
                r = httpx.post(
                    f"{BASE}/documents/upload",
                    files={"files": ("test_api.txt", f, "application/octet-stream")},
                    params={"collection": "default"},
                    timeout=60,
                )
            if r.status_code == 200:
                data = r.json()
                if "tasks" in data and len(data["tasks"]) > 0:
                    ok(f"POST /documents/upload (queued {len(data['tasks'])} tasks)")
                else:
                    fail("POST /documents/upload", "No tasks returned")
            else:
                fail("POST /documents/upload", r.text[:200])
        finally:
            os.unlink(tmp.name)


# ── Config ───────────────────────────────────────────

def test_config():
    with test("Config"):
        # Get config
        r = httpx.get(f"{BASE}/config", timeout=TIMEOUT)
        if r.status_code == 200:
            cfg = r.json()
            if "llm" in cfg and "embedding" in cfg:
                ok("GET /config")
                # Check api_key is masked
                for section in ["llm", "embedding", "rerank"]:
                    if section in cfg and "api_key" in cfg[section]:
                        if cfg[section]["api_key"] in ("***", ""):
                            ok(f"  api_key masked in {section}")
                        else:
                            fail(f"  api_key masked in {section}", cfg[section]["api_key"][:10])
            else:
                fail("GET /config", "missing llm/embedding keys")
        else:
            fail("GET /config", str(r.status_code))

        # Reload config
        r = httpx.post(f"{BASE}/config/reload", timeout=TIMEOUT)
        if r.status_code == 200 and "message" in r.json():
            ok("POST /config/reload")
        else:
            fail("POST /config/reload", r.text[:100])


# ── Query (non-streaming) ───────────────────────────

def test_query():
    with test("Query (non-streaming)"):
        r = httpx.post(
            f"{BASE}/query",
            json={"question": "hello", "collection": "default", "use_agent": False},
            timeout=60,
        )
        if r.status_code == 200:
            data = r.json()
            if "answer" in data and data["answer"]:
                ok(f"POST /query (answer: {data['answer'][:60]}...)")
            else:
                fail("POST /query", "empty answer")
        else:
            fail("POST /query", f"status={r.status_code}")


# ── Query (streaming) ───────────────────────────────

def test_query_stream():
    with test("Query (streaming)"):
        import json as _json
        try:
            tokens = []
            meta = None
            with httpx.Client(timeout=120) as client:
                with client.stream(
                    "POST",
                    f"{BASE}/query/stream",
                    json={"question": "hello", "collection": "default", "use_agent": False},
                ) as response:
                    if response.status_code != 200:
                        fail("POST /query/stream", f"status={response.status_code}")
                        return

                    for line in response.iter_lines():
                        line = line.strip()
                        if not line.startswith("data: "):
                            continue
                        try:
                            payload = _json.loads(line[6:])
                        except (_json.JSONDecodeError, ValueError):
                            continue
                        if payload.get("type") == "meta":
                            meta = payload
                        elif payload.get("type") == "token":
                            tokens.append(payload.get("content", ""))
                        elif payload.get("type") == "error":
                            fail("POST /query/stream", payload.get("content", ""))
                            return
                        elif payload.get("type") == "done":
                            break

            if tokens:
                ok("POST /query/stream ({} tokens)".format(len(tokens)))
            else:
                fail("POST /query/stream", "no tokens received")

            if meta and "sources" in meta:
                ok("  sources: {} items".format(len(meta["sources"])))
            else:
                fail("  sources", "missing meta")

        except Exception as e:
            fail("POST /query/stream", str(e))


# ── Query (Agentic) ──────────────────────────────────

def test_query_agentic():
    with test("Query (Agentic)"):
        r = httpx.post(
            f"{BASE}/query",
            json={"question": "what is python?", "collection": "default", "use_agent": True},
            timeout=120,
        )
        if r.status_code == 200:
            data = r.json()
            if "answer" in data and "iterations" in data:
                ok(f"POST /query agentic (iterations={data['iterations']})")
            else:
                fail("POST /query agentic", "missing fields")
        else:
            fail("POST /query agentic", f"status={r.status_code}")


# ── History ──────────────────────────────────────────

def test_history():
    with test("History"):
        r = httpx.get(f"{BASE}/history", params={"limit": 5}, timeout=TIMEOUT)
        if r.status_code == 200 and isinstance(r.json(), list):
            ok(f"GET /history ({len(r.json())} entries)")
        else:
            fail("GET /history", r.text[:100])


# ── Main ─────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  SinkDuce Backend API Test")
    print("=" * 50)

    try:
        httpx.get(f"http://127.0.0.1:{TEST_PORT}/health", timeout=TIMEOUT)
    except Exception:
        print(f"\nERROR: API server not reachable at http://127.0.0.1:{TEST_PORT}")
        print("Start the server first: docker compose up -d app")
        sys.exit(1)

    test_health()
    test_config()
    test_collections()
    test_documents()
    test_upload()
    test_query()
    test_query_stream()
    test_query_agentic()
    test_history()

    print("\n" + "=" * 50)
    total = PASS + FAIL
    print(f"  Results: {PASS}/{total} passed, {FAIL} failed")
    print("=" * 50)

    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
