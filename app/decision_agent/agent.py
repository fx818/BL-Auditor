"""Decision Agent — deterministic final verdict from 4 Windmill agent statuses.

Takes the outputs of the 4 product-validation agents (with optional recheck data)
and produces a single binary Final_Verdict.

Returns:
    {"Final_Verdict": "outlier" | "not_outlier", "Reason": str}
"""
from typing import Any, Dict, List

_AGENT_NAME_MAP: Dict[str, str] = {
    "title_vs_category": "Title vs MCAT Agent",
    "title_vs_specs":    "Title ISQ Agent",
    "specs_vs_category": "ISQ vs MCAT Agent",
    "isq_validation":    "Inter ISQ Agent",
}

_PRODUCT_AGENTS = [
    "Title vs MCAT Agent",
    "ISQ vs MCAT Agent",
    "Title ISQ Agent",
    "Inter ISQ Agent",
]

# Maps Windmill result keys → Decision Agent display names
_WM_KEY_TO_AGENT: Dict[str, str] = {
    "title_category_outlier": "Title vs MCAT Agent",
    "specs_category_outlier": "ISQ vs MCAT Agent",
    "title_spec_verdict":     "Title ISQ Agent",
}


def _normalize_status(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in ("wrong", "outlier", "incorrect"):
        return "outlier"
    if s in ("correct", "not_outlier"):
        return "not_outlier"
    return ""


def run_decision_agent(
    windmill_result: Dict[str, Any],
    isq_result: Dict[str, Any],
    recheck_flagged: List[str],
    recheck_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Combine 4 agent statuses + recheck into a Final_Verdict.

    windmill_result — raw output from call_windmill_agents
    isq_result      — from run_isq_validation_agent
    recheck_flagged — internal keys flagged for recheck (e.g. ["title_vs_category"])
    recheck_results — recheck_result["results"]: [{"agent": display_name, "status": ..., "reason": ...}]
    """
    windmill_result = windmill_result or {}
    isq_result = isq_result or {}

    # Build correct dict: {display_name: {"status": "outlier"|"not_outlier"}}
    correct: Dict[str, Dict[str, str]] = {}
    for wm_key, agent_name in _WM_KEY_TO_AGENT.items():
        block = windmill_result.get(wm_key) or {}
        raw = block.get("status") or block.get("final_verdict") or ""
        status = _normalize_status(raw)
        if status:
            correct[agent_name] = {"status": status}

    isq_status = _normalize_status(
        isq_result.get("status") or isq_result.get("Status") or ""
    )
    if isq_status:
        correct["Inter ISQ Agent"] = {"status": isq_status}

    # Build incorrect list (display names) from internal keys
    incorrect = [_AGENT_NAME_MAP[k] for k in (recheck_flagged or []) if k in _AGENT_NAME_MAP]

    # 1. Read statuses from correct dict
    agent_status: Dict[str, str] = {}
    for agent in _PRODUCT_AGENTS:
        obj = correct.get(agent)
        if isinstance(obj, dict):
            status = obj.get("status")
            if status:
                agent_status[agent] = status

    # 2. Build recheck lookup
    recheck_lookup: Dict[str, str] = {}
    for item in recheck_results or []:
        if not isinstance(item, dict):
            continue
        name = item.get("agent") or item.get("Agent")
        status = item.get("status")
        if name and status:
            recheck_lookup[name] = status

    # 3. Override status for incorrect agents
    for agent in incorrect:
        if agent not in _PRODUCT_AGENTS:
            continue
        if agent in recheck_lookup:
            agent_status[agent] = recheck_lookup[agent]
        else:
            agent_status[agent] = "outlier"

    # 4. Final decision
    failed_agents = [a for a, s in agent_status.items() if s == "outlier"]

    if failed_agents:
        return {
            "Final_Verdict": "outlier",
            "Reason": f"{', '.join(failed_agents)} returned outlier.",
        }

    return {
        "Final_Verdict": "not_outlier",
        "Reason": "All product validation agents are correct.",
    }
