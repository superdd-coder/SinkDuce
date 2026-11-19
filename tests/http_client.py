"""Clients for tests exercising routes as the local box owner."""

from fastapi.testclient import TestClient

from src.pairing.service import local_entry_port


def local_owner_client(app) -> TestClient:
    return TestClient(
        app, base_url=f"http://127.0.0.1:{local_entry_port()}",
        client=("127.0.0.1", 51000),
    )
