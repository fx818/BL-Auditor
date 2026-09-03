import logging
import os
from contextvars import ContextVar
from typing import Any

log = logging.getLogger("bl-auditor.langfuse")

_handler_var: ContextVar = ContextVar("langfuse_handler", default=None)


def _keys_present() -> bool:
    return bool(
        os.getenv("LANGFUSE_PUBLIC_KEY")
        and os.getenv("LANGFUSE_SECRET_KEY")
        and os.getenv("LANGFUSE_BASE_URL")
    )


def make_langfuse_handler(offer_id: str, user_id: str | None = None):
    if not _keys_present():
        return None
    try:
        import uuid
        from langfuse.langchain import CallbackHandler
        # v4: constructor only accepts public_key and trace_context.
        # Use a deterministic UUID from offer_id as trace_id so all agents
        # for the same audit appear under one trace in the Langfuse UI.
        trace_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bl-audit:{offer_id}").hex
        return CallbackHandler(trace_context={"trace_id": trace_id})
    except Exception as exc:
        log.warning("Langfuse handler init failed: %s", exc)
        return None


def set_langfuse_handler(handler: Any) -> Any:
    return _handler_var.set(handler)


def get_langfuse_handler() -> Any:
    return _handler_var.get()


def reset_langfuse_handler(token: Any) -> None:
    _handler_var.reset(token)
