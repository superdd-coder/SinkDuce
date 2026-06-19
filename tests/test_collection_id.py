#!/usr/bin/env python3
"""Collection ID/Name mapping integration tests.

Run: python3 tests/test_collection_id.py
Requires: API server running on http://127.0.0.1:18920
"""

import os
import sys
import httpx

TEST_PORT = os.environ.get("SINKDUCE_API_PORT", "18920")
BASE = f"http://127.0.0.1:{TEST_PORT}/api"
TIMEOUT = 30
PASS = 0
FAIL = 0
COL_ID = None  # Store created collection ID


def ok(name: str):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}")


def fail(name: str, detail: str = ""):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name}: {detail}")


def test(label: str):
    class _Ctx:
        def __enter__(self):
            print(f"\n{label}")
            return self
        def __exit__(self, *a):
            pass
    return _Ctx()


def test_create_collection():
    global COL_ID
    with test("Create Collection"):
        r = httpx.post(f"{BASE}/collections", json={"name": "Test ID Mapping"}, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            COL_ID = data.get("id")
            if COL_ID and COL_ID.startswith("col_"):
                ok(f"Created collection with ID: {COL_ID}")
            else:
                fail("Create collection", f"Invalid ID: {COL_ID}")
        else:
            fail("Create collection", r.text[:100])


def test_list_collections():
    with test("List Collections"):
        r = httpx.get(f"{BASE}/collections", timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            # Check all items have id and name
            all_have_fields = all("id" in c and "name" in c for c in data)
            if all_have_fields:
                ok("All collections have id and name fields")
            else:
                fail("Missing fields", "Some collections missing id or name")

            # Check our created collection is in the list
            found = any(c["id"] == COL_ID and c["name"] == "Test ID Mapping" for c in data)
            if found:
                ok("Created collection found with correct ID and name")
            else:
                fail("Created collection not found")
        else:
            fail("List collections", f"status={r.status_code}")


def test_rename_collection():
    with test("Rename Collection"):
        r = httpx.put(f"{BASE}/collections/{COL_ID}/rename",
                      json={"name": "Renamed Test"}, timeout=TIMEOUT)
        if r.status_code == 200:
            ok("Rename successful")
        else:
            fail("Rename", r.text[:100])


def test_verify_rename():
    with test("Verify Rename"):
        r = httpx.get(f"{BASE}/collections", timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            found = any(c["id"] == COL_ID and c["name"] == "Renamed Test" for c in data)
            if found:
                ok("Rename persisted (ID unchanged, name updated)")
            else:
                fail("Rename not persisted")

            # Verify ID didn't change
            id_found = any(c["id"] == COL_ID for c in data)
            if id_found:
                ok("ID unchanged after rename")
            else:
                fail("ID changed after rename")
        else:
            fail("Verify rename", f"status={r.status_code}")


def test_config_api():
    with test("Config API"):
        # Get config
        r = httpx.get(f"{BASE}/collections/{COL_ID}/config", timeout=TIMEOUT)
        if r.status_code == 200:
            ok("GET /collections/{id}/config")
        else:
            fail("GET config", r.text[:100])

        # Update config
        r = httpx.put(f"{BASE}/collections/{COL_ID}/config",
                      json={"search_mode": "hybrid"}, timeout=TIMEOUT)
        if r.status_code == 200:
            ok("PUT /collections/{id}/config")
        else:
            fail("PUT config", r.text[:100])


def test_info_api():
    with test("Info API"):
        r = httpx.get(f"{BASE}/collections/{COL_ID}/info", timeout=TIMEOUT)
        if r.status_code == 200:
            ok("GET /collections/{id}/info")
        else:
            fail("GET info", r.text[:100])


def test_delete_collection():
    with test("Delete Collection"):
        r = httpx.delete(f"{BASE}/collections/{COL_ID}", timeout=TIMEOUT)
        if r.status_code == 200:
            ok("DELETE /collections/{id}")
        else:
            fail("DELETE", r.text[:100])


def test_verify_deletion():
    with test("Verify Deletion"):
        r = httpx.get(f"{BASE}/collections", timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            found = any(c["id"] == COL_ID for c in data)
            if not found:
                ok("Collection removed from list")
            else:
                fail("Collection still in list after deletion")
        else:
            fail("Verify deletion", f"status={r.status_code}")


def test_no_duplicates():
    with test("No Duplicates"):
        r = httpx.get(f"{BASE}/collections", timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            ids = [c["id"] for c in data]
            duplicates = [k for k in set(ids) if ids.count(k) > 1]
            if not duplicates:
                ok("No duplicate collection IDs")
            else:
                fail("Duplicates found", str(duplicates))
        else:
            fail("Check duplicates", f"status={r.status_code}")


def test_legacy_collections():
    with test("Legacy Collections"):
        r = httpx.get(f"{BASE}/collections", timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            for c in data:
                if not c.get("id") or not c.get("name"):
                    fail("Missing fields", f"Collection: {c}")
                    return
            ok(f"All {len(data)} collections have id and name")
        else:
            fail("List collections", f"status={r.status_code}")


def main():
    print("=" * 50)
    print("  Collection ID/Name Mapping Tests")
    print("=" * 50)

    try:
        httpx.get(f"http://127.0.0.1:{TEST_PORT}/health", timeout=TIMEOUT)
    except Exception:
        print(f"\nERROR: API server not reachable at http://127.0.0.1:{TEST_PORT}")
        print("Start the server first: docker compose up -d app")
        sys.exit(1)

    test_create_collection()
    test_list_collections()
    test_rename_collection()
    test_verify_rename()
    test_config_api()
    test_info_api()
    test_delete_collection()
    test_verify_deletion()
    test_no_duplicates()
    test_legacy_collections()

    print("\n" + "=" * 50)
    total = PASS + FAIL
    print(f"  Results: {PASS}/{total} passed, {FAIL} failed")
    print("=" * 50)

    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
