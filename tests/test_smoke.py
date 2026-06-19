"""Smoke test: run with `docker compose up` then execute this.
Set SINKDUCE_API_PORT env var to override default (18900)."""
import os
import httpx
import sys

PORT = os.environ.get("SINKDUCE_API_PORT", "18900")
BASE = f"http://localhost:{PORT}"


def test_health():
    r = httpx.get(f"{BASE}/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    print("Health check passed")


def test_collections():
    r = httpx.get(f"{BASE}/api/collections")
    assert r.status_code == 200
    print(f"Collections: {r.json()}")


def test_create_collection():
    r = httpx.post(f"{BASE}/api/collections", json={"name": "test"})
    assert r.status_code == 200
    print("Created test collection")


def test_query():
    r = httpx.post(
        f"{BASE}/api/query",
        json={"question": "hello", "collection": "default"},
        timeout=60,
    )
    assert r.status_code == 200
    print(f"Query response: {r.json()['answer'][:100]}")


if __name__ == "__main__":
    try:
        test_health()
        test_collections()
        test_create_collection()
        test_query()
        print("\nAll smoke tests passed!")
    except Exception as e:
        print(f"\nTest failed: {e}")
        sys.exit(1)
