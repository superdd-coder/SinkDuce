import os
import pytest

collect_ignore = ["test_api.py", "test_smoke.py", "test_ui_automation.py"]


@pytest.fixture(scope="session")
def api_base():
    port = os.environ.get("SINKDUCE_API_PORT", "18900")
    return f"http://localhost:{port}"
