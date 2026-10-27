from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.errors import ApiError


def test_api_error_json_shape():
    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise ApiError(
            404,
            "file_not_found",
            "File not found for source 'notes.pdf'",
            params={"source": "notes.pdf"},
        )

    client = TestClient(app)
    res = client.get("/boom")
    assert res.status_code == 404
    assert res.json() == {
        "detail": {
            "code": "file_not_found",
            "params": {"source": "notes.pdf"},
            "message": "File not found for source 'notes.pdf'",
        }
    }
