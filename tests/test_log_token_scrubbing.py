"""Access-log token scrubbing + staging token redaction (2026-09-19 P0).

链路：SSE/WS 只能把配对钥匙放 query string → uvicorn access log 整行落盘
→ 日志文件/日志流订阅者可捞走长钥匙。修复后 access log 里 ``token=`` 的值
必须被抹掉；staging token 只打前 8 位。
"""

from __future__ import annotations

import logging

from src.main import _QueryTokenScrubFilter


def _scrub(message: str) -> str:
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, message, (), None
    )
    _QueryTokenScrubFilter().filter(record)
    return record.getMessage()


def test_long_pairing_token_is_scrubbed():
    line = '127.0.0.1:1 - "GET /api/events/stream?token=sd1_aaaa bbbb cccc HTTP/1.1" 200'
    # token 值里没有空格——这里用一条真实形状的请求行
    line = '127.0.0.1:1 - "GET /api/events/stream?token=sd1_abcdef1234567890 HTTP/1.1" 200 OK'
    out = _scrub(line)
    assert "sd1_abcdef1234567890" not in out
    assert "token=***" in out
    assert "/api/events/stream" in out
    assert "200 OK" in out


def test_short_ticket_is_scrubbed():
    out = _scrub('x - "GET /api/logs/stream?token=sda1_zzz999&client_id=c1 HTTP/1.1" 200')
    assert "sda1_zzz999" not in out
    assert "client_id=c1" in out  # 其他参数原样保留


def test_plain_request_untouched():
    line = 'x - "GET /api/collections HTTP/1.1" 200 OK'
    assert _scrub(line) == line


def test_other_query_params_untouched():
    out = _scrub('x - "GET /api/file-mgmt/col_x/files?folder_id=f1 HTTP/1.1" 200')
    assert out == 'x - "GET /api/file-mgmt/col_x/files?folder_id=f1 HTTP/1.1" 200'


def test_filter_installed_on_uvicorn_access_logger():
    assert any(
        isinstance(f, _QueryTokenScrubFilter) for f in logging.getLogger("uvicorn.access").filters
    )


def test_staging_log_lines_only_show_token_prefix():
    import inspect

    from src.mcp import staging_routes

    source = inspect.getsource(staging_routes._stage)
    assert "token[:8]" in source
    assert 'token=%s' not in source.replace("token=%s…", "")
