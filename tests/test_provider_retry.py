from src.providers.retry import is_rate_limit_error, retry_delay, retry_on_rate_limit


def test_is_timeout_error_detects_sdk_and_builtin():
    from src.providers.retry import is_timeout_error

    class APITimeoutError(Exception):
        pass

    assert is_timeout_error(TimeoutError("read timed out"))
    assert is_timeout_error(APITimeoutError("Request timed out"))
    assert not is_timeout_error(RuntimeError("429 rate limit"))


def test_is_rate_limit_error_detects_common_shapes():
    class _HTTP:
        status_code = 429

    assert is_rate_limit_error(_HTTP())
    assert is_rate_limit_error(RuntimeError("Error code: 429 - throttling"))
    assert is_rate_limit_error(RuntimeError("RateQuota exceeded"))
    assert not is_rate_limit_error(RuntimeError("connection reset"))


def test_retry_delay_grows_exponentially(monkeypatch):
    monkeypatch.setattr("src.providers.retry.random.uniform", lambda a, b: 0.0)
    assert retry_delay(1) == 2.0
    assert retry_delay(2) == 4.0
    assert retry_delay(3) == 8.0


def test_retry_on_rate_limit_succeeds_after_backoff(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("src.providers.retry.time.sleep", sleeps.append)
    monkeypatch.setattr("src.providers.retry.random.uniform", lambda a, b: 0.0)
    hits = {"n": 0}

    def _flaky():
        hits["n"] += 1
        if hits["n"] < 3:
            err = RuntimeError("429 rate limit")
            raise err
        return "ok"

    assert retry_on_rate_limit(_flaky, description="test") == "ok"
    assert hits["n"] == 3
    assert sleeps == [2.0, 4.0]


def test_retry_on_rate_limit_raises_non_429():
    def _boom():
        raise ValueError("bad json")

    try:
        retry_on_rate_limit(_boom)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "bad json" in str(exc)
