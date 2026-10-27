"""SIN-85 — MinerU token expiry must not stay silent.

MinerU API keys lapse after ~90 days; the ingest handler falls back to the
local parser so the upload still succeeds. The expiry signal rides the task's
``warnings`` list so the UI can toast a renewal hint.
"""

import httpx
import pytest

from src.parsers.mineru_parser import MinerUError, MinerUParser
from src.tasks.task_manager import Task, TaskManager, TaskStatus


def _resp(status: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        json=body if body is not None else {"code": 0, "msg": "ok"},
        request=httpx.Request("POST", "https://mineru.net/api/v4/x"),
    )


class TestIsAuthError:
    def test_http_401_and_403(self):
        assert MinerUError("unauthorized", code=401).is_auth_error
        assert MinerUError("forbidden", code=403).is_auth_error

    def test_other_http_codes_are_not_auth(self):
        assert not MinerUError("rate limit", code=429).is_auth_error
        assert not MinerUError("server error", code=500).is_auth_error

    def test_string_codes_with_token_or_auth(self):
        assert MinerUError("no token", code="TOKEN_EXPIRED").is_auth_error
        assert MinerUError("denied", code="AUTHORIZATION_ERROR").is_auth_error
        # MinerU business codes are 0 on success; treat unknown strings as
        # non-auth so generic failures do not nag the user about renewal.
        assert not MinerUError("boom", code="INTERNAL_ERROR").is_auth_error

    def test_missing_code_is_not_auth(self):
        assert not MinerUError("plain failure").is_auth_error


class TestCheckResponse:
    def test_401_raises_auth_error(self):
        with pytest.raises(MinerUError) as ei:
            MinerUParser._check_response(
                _resp(401, {"code": 401, "msg": "token is invalid"})
            )
        assert ei.value.is_auth_error

    def test_403_raises_auth_error(self):
        with pytest.raises(MinerUError) as ei:
            MinerUParser._check_response(
                _resp(403, {"code": "AUTH_FAILED", "msg": "expired"})
            )
        assert ei.value.is_auth_error

    def test_429_is_not_auth(self):
        with pytest.raises(MinerUError) as ei:
            MinerUParser._check_response(_resp(429, {"code": 429, "msg": "slow down"}))
        assert not ei.value.is_auth_error

    def test_200_business_auth_code_is_auth(self):
        with pytest.raises(MinerUError) as ei:
            MinerUParser._check_response(
                _resp(200, {"code": "TOKEN_INVALID", "msg": "renew"})
            )
        assert ei.value.is_auth_error


class TestTaskWarnings:
    def test_to_dict_includes_warnings(self):
        task = Task(id="t1", filename="a.pdf", collection="c")
        assert task.warnings == []
        assert task.to_dict()["warnings"] == []
        task.warnings.append("mineru_token_invalid")
        assert task.to_dict()["warnings"] == ["mineru_token_invalid"]

    def test_to_dict_warnings_is_a_copy(self):
        task = Task(id="t1", filename="a.pdf")
        task.warnings.append("x")
        d = task.to_dict()
        d["warnings"].append("y")
        assert task.warnings == ["x"]

    def test_requeue_clears_warnings(self):
        import asyncio

        async def scenario():
            tm = TaskManager()
            task = Task(id="t1", filename="a.pdf", status=TaskStatus.FAILED)
            task.warnings.append("mineru_token_invalid")
            tm.tasks["t1"] = task
            tm._task_args["t1"] = ("noop", {})
            # sync method; needs a running loop for its internal create_task
            tm.retry_task("t1")
            return task

        task = asyncio.run(scenario())
        assert task.warnings == []
        assert task.status == TaskStatus.PENDING
