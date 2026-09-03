"""User-editable prompt overrides for the three classification agents.

Active prompt = override file under ``data/prompt_overrides/`` if present and
non-empty, else the bundled ``prompt.md`` next to the agent module. Bundled
defaults are never modified.
"""

from __future__ import annotations

import os
import tempfile
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Dict, Tuple

ROOT_DIR = Path(__file__).resolve().parents[2]
_DATA_DIR = Path(os.environ.get("DATA_DIR") or ROOT_DIR)
OVERRIDE_DIR = _DATA_DIR / "prompt_overrides"

_current_user: ContextVar[str | None] = ContextVar("_current_user", default=None)


def set_audit_user(email: str | None) -> Token:
    return _current_user.set((email or "").strip().lower() or None)


def reset_audit_user(token: Token) -> None:
    _current_user.reset(token)


AGENTS: Dict[str, Dict] = {
    "buyer_viewed": {
        "display_name": "Buyer Viewed Agent",
        "bundled_path": ROOT_DIR / "app" / "buyer_viewed_agent" / "prompt.md",
        "placeholders": [
            "Display_id", "Title", "MCAT", "MCAT_id",
            "PRODUCTS_ENQUIRED", "Products_Enquired_Count",
        ],
    },
    "retail_agent_2": {
        "display_name": "Retail Agent 2",
        "bundled_path": ROOT_DIR / "app" / "retail_agent_2" / "prompt.md",
        "placeholders": [],
    },
    "isq_validation": {
        "display_name": "ISQ Validation Agent",
        "bundled_path": ROOT_DIR / "app" / "isq_validation_agent" / "prompt.md",
        "placeholders": ["mcat_name", "item_name", "isq_table"],
    },
    "description": {
        "display_name": "Description Agent",
        "bundled_path": ROOT_DIR / "app" / "description_agent" / "prompt.md",
        "placeholders": ["mcat_name", "item_name", "description"],
    },
    "buyer_profile": {
        "display_name": "Buyer Profile Agent",
        "bundled_path": ROOT_DIR / "app" / "buyer_profile_agent" / "prompt.md",
        "placeholders": ["current_bl", "prev_buyleads", "prev_enquiries"],
    },
    "scoring": {
        "display_name": "BuyLead Score Weights",
        "bundled_path": ROOT_DIR / "app" / "scoring_agent" / "prompt.md",
        "placeholders": [],
    },
    "specs_vs_category_agent2": {
        "display_name": "Specs vs Category (Agent 2)",
        "bundled_path": ROOT_DIR / "app" / "specs_vs_category_agent2" / "prompt.md",
        "placeholders": ["mcat_name", "isq_table"],
    },
    "title_vs_category_agent2": {
        "display_name": "Title vs Category (Agent 2)",
        "bundled_path": ROOT_DIR / "app" / "title_vs_category_agent2" / "prompt.md",
        "placeholders": ["mcat_name", "title"],
    },
    "title_vs_specs_agent2": {
        "display_name": "Title vs Specs (Agent 2)",
        "bundled_path": ROOT_DIR / "app" / "title_vs_specs_agent2" / "prompt.md",
        "placeholders": ["title", "isq_table"],
    },
}

ADMIN_ONLY_AGENTS: set = set()
PUBLIC_AGENTS = [k for k in AGENTS if k not in ADMIN_ONLY_AGENTS]


def _require(agent_key: str) -> Dict:
    if agent_key not in AGENTS:
        raise KeyError(f"Unknown agent: {agent_key}")
    return AGENTS[agent_key]


def override_path(agent_key: str) -> Path:
    _require(agent_key)
    return OVERRIDE_DIR / f"{agent_key}.md"


def user_override_path(agent_key: str, user_email: str) -> Path:
    _require(agent_key)
    safe = user_email.strip().lower()
    if "/" in safe or "\\" in safe or ".." in safe:
        raise ValueError(f"Invalid email for path: {user_email!r}")
    return OVERRIDE_DIR / safe / f"{agent_key}.md"


def bundled_path(agent_key: str) -> Path:
    return _require(agent_key)["bundled_path"]


def get_active_prompt(agent_key: str, user_email: str | None = None) -> Tuple[str, bool]:
    """Return ``(prompt_text, is_override)`` for the agent.

    Fallback order: user-scoped override → global override → bundled default.
    ``user_email`` overrides the ContextVar when provided explicitly.
    """
    email = user_email or _current_user.get()
    if email:
        try:
            text = user_override_path(agent_key, email).read_text(encoding="utf-8")
            if text.strip():
                return text, True
        except FileNotFoundError:
            pass
    try:
        text = override_path(agent_key).read_text(encoding="utf-8")
        if text.strip():
            return text, True
    except FileNotFoundError:
        pass
    return bundled_path(agent_key).read_text(encoding="utf-8"), False


def get_bundled_prompt(agent_key: str) -> str:
    return bundled_path(agent_key).read_text(encoding="utf-8")


def save_override(agent_key: str, content: str, user_email: str | None = None) -> None:
    """Atomically write the override file (user-scoped if email given, else global)."""
    _require(agent_key)
    email = user_email or _current_user.get()
    target = user_override_path(agent_key, email) if email else override_path(agent_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{agent_key}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def reset_override(agent_key: str, user_email: str | None = None) -> None:
    email = user_email or _current_user.get()
    ov = user_override_path(agent_key, email) if email else override_path(agent_key)
    if ov.exists():
        ov.unlink()


def list_agents() -> Dict[str, Dict]:
    return AGENTS
