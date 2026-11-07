"""Optional UI-only preview companion for an older running backend.

Run inside the existing app image with this worktree mounted at /workspace and
SINKDUCE_DATA mounted read-only. Publish only to 127.0.0.1; use the app's normal
internal local port (18900). Set SEARCH_API_PORT in Vite to the published port.
Production uses src.main and does not need this companion.
"""
from fastapi import FastAPI
from src.pairing.middleware import PairingGateMiddleware, assert_gate_route_coverage
from src.search.routes import router

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(PairingGateMiddleware)
app.include_router(router, prefix='/api')
assert_gate_route_coverage(app)
