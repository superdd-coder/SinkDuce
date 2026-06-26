"""RAG modules — shared context for log tracing."""
import threading

_ctx = threading.local()


def set_log_ctx(ctx: str) -> None:
    """Set per-thread log context (e.g. ``"[t1/aq1]"``). Clear with ``""``."""
    _ctx.value = ctx


def get_log_ctx() -> str:
    """Return current per-thread log context, or ``""`` if unset."""
    return getattr(_ctx, "value", "")
