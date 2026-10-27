"""End-to-end check: real uvicorn, two sockets, no mocks.

端口分工（容器形态）：
- 主端口 18900 = **本机入口**（宿主机只发布到回环）→ 领钥匙端点在此可达；
- 对外端口 18901 = **局域网入口** → 需钥匙。

Proves the core promise: a request arriving on the local-entry port gets a key with
no credential, while the same request on the external port is refused.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _lan_ip() -> str | None:
    """本机默认路由源地址，用来模拟「局域网里的另一台设备」。无网卡则 None。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("223.5.5.5", 80))  # 不发包，仅取源地址
            ip = s.getsockname()[0]
        return ip if not ip.startswith("127.") else None
    except OSError:
        return None


def _get(url: str, headers: dict[str, str] | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """Launch the real app in container mode: both ports on 0.0.0.0 (as in Docker)."""
    entry_port = _free_port()
    external_port = _free_port()
    data_dir = tmp_path_factory.mktemp("pairing-e2e")
    env = {
        **os.environ,
        "SINKDUCE_DATA": str(data_dir),
        "SINKDUCE_HOST": "0.0.0.0",
        "SINKDUCE_PORT": str(entry_port),
        "SINKDUCE_EXTERNAL_PORT": str(external_port),
        "SINKDUCE_CONTAINER": "1",  # 强制容器形态：开第二个（对外）监听
        "SINKDUCE_FRONTEND_DIST": str(data_dir / "nope"),  # skip SPA mount
        "PYTHONPATH": str(REPO),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.main"],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 40
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode() if proc.stdout else ""
            pytest.fail(f"server exited early:\n{out}")
        try:
            with socket.create_connection(("127.0.0.1", entry_port), timeout=0.5):
                with socket.create_connection(("127.0.0.1", external_port), timeout=0.5):
                    break
        except OSError:
            time.sleep(0.5)
    else:
        proc.terminate()
        pytest.fail("server did not come up")

    yield {"entry_port": entry_port, "external_port": external_port, "data_dir": data_dir}

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


class TestRealListenerStacks:
    def test_local_entry_hands_out_key_without_credential(self, server):
        url = f"http://127.0.0.1:{server['entry_port']}/api/pairing/local-key"
        status, body = _get(url, {"Origin": f"http://localhost:{server['entry_port']}"})
        assert status == 200, body
        assert body["token"].startswith("sd1_")

    def test_key_endpoint_rejects_lan_host_header(self, server):
        # 即使主端口被误发布，Host 不是回环也拿不到钥匙。
        # 用 TEST-NET 段模拟邻居（RFC 5737）：具体私网 IP 会与开发机真实网卡撞车。
        url = f"http://127.0.0.1:{server['entry_port']}/api/pairing/local-key"
        status, _ = _get(url, {"Host": "198.51.100.7:18900"})
        assert status == 403

    def test_external_port_refuses_anonymous_key_request(self, server):
        lan = _lan_ip()
        if not lan:
            pytest.skip("no non-loopback address to simulate a LAN peer")
        status, _ = _get(f"http://{lan}:{server['external_port']}/api/pairing/local-key")
        assert status == 401

    def test_loopback_client_is_trusted_on_either_port(self, server):
        # 回环来源即本机（桌面语义），两个端口都放行——本机永远进得去
        status_local, _ = _get(f"http://127.0.0.1:{server['entry_port']}/api/pairing/tokens")
        status_external, _ = _get(f"http://127.0.0.1:{server['external_port']}/api/pairing/tokens")
        assert status_local == 200
        assert status_external == 200

    def test_external_port_refuses_other_api_without_key(self, server):
        lan = _lan_ip()
        if not lan:
            pytest.skip("no non-loopback address to simulate a LAN peer")
        status, _ = _get(f"http://{lan}:{server['external_port']}/api/collections")
        assert status == 401

    def test_key_from_local_entry_opens_external_api(self, server):
        lan = _lan_ip()
        if not lan:
            pytest.skip("no non-loopback address to simulate a LAN peer")
        _, key = _get(f"http://127.0.0.1:{server['entry_port']}/api/pairing/local-key")
        status, body = _get(
            f"http://{lan}:{server['external_port']}/api/pairing/box",
            {"X-Pairing-Token": key["token"]},
        )
        assert status == 200, body
        assert body["box_id"]

    def test_local_key_file_written_for_recovery(self, server):
        _get(f"http://127.0.0.1:{server['entry_port']}/api/pairing/local-key")
        key_file = server["data_dir"] / "pairing" / "local-key.txt"
        assert key_file.exists()
        assert key_file.read_text().strip().startswith("sd1_")

    def test_health_reports_external_port(self, server):
        status, body = _get(f"http://127.0.0.1:{server['entry_port']}/health")
        assert status == 200
        assert body["external_port"] == server["external_port"]

    def test_key_is_stable_after_restart_path(self, server):
        _, a = _get(f"http://127.0.0.1:{server['entry_port']}/api/pairing/local-key")
        _, b = _get(f"http://127.0.0.1:{server['entry_port']}/api/pairing/local-key")
        assert a["token"] == b["token"]
