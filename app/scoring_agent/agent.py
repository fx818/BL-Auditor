"""
Deterministic BuyLead composite scoring agent.

Weights and thresholds are read from the scoring prompt.md YAML block — edit
weights there (via the prompts UI) to change computation without a code deploy.
No LLM call — pure math.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

CORRECT_MULT: Dict[str, float] = {"high": 1.0, "medium": 0.6, "low": 0.3}
INCORRECT_MULT: Dict[str, float] = {"high": 0.0, "medium": 0.2, "low": 0.4}

_DEFAULT_WEIGHTS: Dict[str, float] = {
    "specs_vs_category": 20,
    "title_vs_category": 20,
    "title_vs_specs": 15,
    "isq_validation": 15,
    "retail_classification": 10,
    "description_coherence": 10,
}
_DEFAULT_THRESHOLDS = {"approved": 75, "reject": 30}

BINARY_KEYS = {"specs_vs_category", "title_vs_category", "title_vs_specs"}

_DISPLAY_NAMES = {
    "specs_vs_category": "Specs vs Category",
    "title_vs_category": "Title vs Category",
    "title_vs_specs": "Title vs Specs",
    "isq_validation": "ISQ Validation",
    "retail_classification": "Retail Classification",
    "description_coherence": "Description Coherence",
}


def _parse_config() -> tuple[Dict[str, float], Dict[str, int]]:
    """Parse weights and thresholds from the active scoring prompt YAML block."""
    try:
        from app.services.prompt_override_service import get_active_prompt
        content, _ = get_active_prompt("scoring")
        match = re.search(r"```yaml\s*\n(.*?)```", content, re.DOTALL)
        if not match:
            return _DEFAULT_WEIGHTS.copy(), _DEFAULT_THRESHOLDS.copy()
        block = match.group(1)

        weights: Dict[str, float] = {}
        thresholds: Dict[str, int] = {}
        section = None
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped == "weights:":
                section = "weights"
                continue
            if stripped == "thresholds:":
                section = "thresholds"
                continue
            if ":" in stripped:
                k, v = stripped.split(":", 1)
                k, v = k.strip().lstrip("- "), v.strip()
                try:
                    if section == "weights":
                        weights[k] = float(v)
                    elif section == "thresholds":
                        thresholds[k] = int(float(v))
                except ValueError:
                    pass

        final_weights = weights if len(weights) == len(_DEFAULT_WEIGHTS) else _DEFAULT_WEIGHTS.copy()
        final_thresholds = {
            "approved": thresholds.get("approved", _DEFAULT_THRESHOLDS["approved"]),
            "reject": thresholds.get("reject", _DEFAULT_THRESHOLDS["reject"]),
        }
        return final_weights, final_thresholds
    except Exception:
        return _DEFAULT_WEIGHTS.copy(), _DEFAULT_THRESHOLDS.copy()


def _conf_norm(c: Any) -> Optional[str]:
    if not c or str(c).lower() in ("none", "null", ""):
        return None
    return str(c).lower()


def _get(d: Dict, *keys: str, default: str = "") -> str:
    """Case-insensitive get — tries each key in order."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return str(v).strip()
    return default


def _map_inputs(
    audit_result: Dict,
    isq_result: Dict,
    desc_result: Dict,
    retail2_result: Dict,
) -> Dict[str, Dict[str, Any]]:
    """Map existing agent result dicts to scoring inputs."""
    agents: Dict[str, Dict[str, Any]] = {}

    # --- Binary: Specs vs Category ---
    svc_status = _get((audit_result.get("specs_category_outlier") or {}), "status").lower()
    if svc_status in ("correct", "not_outlier", "not-outlier", "not outlier"):
        agents["specs_vs_category"] = {"status": "correct", "confidence": None}
    elif svc_status in ("outlier", "wrong", "incorrect", "not_correct"):
        agents["specs_vs_category"] = {"status": "not_correct", "confidence": None}
    else:
        agents["specs_vs_category"] = {"status": "na", "confidence": None}

    # --- Binary: Title vs Category ---
    tvc_status = _get((audit_result.get("title_category_outlier") or {}), "status").lower()
    if tvc_status in ("correct", "not_outlier", "not-outlier", "not outlier"):
        agents["title_vs_category"] = {"status": "correct", "confidence": None}
    elif tvc_status in ("outlier", "wrong", "incorrect", "not_correct"):
        agents["title_vs_category"] = {"status": "not_correct", "confidence": None}
    else:
        agents["title_vs_category"] = {"status": "na", "confidence": None}

    # --- Binary: Title vs Specs (from audit API title_spec_verdict.final_verdict) ---
    tvs_verdict = _get((audit_result.get("title_spec_verdict") or {}), "final_verdict").lower()
    if tvs_verdict in ("correct", "not_outlier", "not-outlier", "not outlier"):
        agents["title_vs_specs"] = {"status": "correct", "confidence": None}
    elif tvs_verdict in ("outlier", "wrong", "incorrect", "not_correct"):
        agents["title_vs_specs"] = {"status": "not_correct", "confidence": None}
    else:
        agents["title_vs_specs"] = {"status": "na", "confidence": None}

    # --- ISQ Validation (binary: outlier|not_outlier, no confidence field) ---
    isq_s = _get(isq_result, "status")
    if isq_s == "not_outlier":
        agents["isq_validation"] = {"status": "correct", "confidence": "high"}
    elif isq_s == "outlier":
        agents["isq_validation"] = {"status": "incorrect", "confidence": "high"}
    else:
        agents["isq_validation"] = {"status": "na", "confidence": None}

    # --- Confidence: Retail Classification (from retail_agent_2) ---
    rc_cls = _get(retail2_result, "Classification", "classification")
    rc_c = _conf_norm(_get(retail2_result, "Confidence", "confidence"))
    if rc_cls in ("Retail", "Non-Retail", "RETAIL", "NON-RETAIL"):
        agents["retail_classification"] = {"status": "correct", "confidence": rc_c or "high"}
    elif rc_cls in ("UNCLASSIFIED", ""):
        agents["retail_classification"] = {"status": "na", "confidence": None}
    else:
        agents["retail_classification"] = {"status": "incorrect", "confidence": rc_c}

    # --- Confidence: Description Coherence ---
    # Description agent now returns a binary {status: outlier|not_outlier}; no
    # confidence gradient. Pin confidence "high" so not_outlier→1.0, outlier→0.0.
    dc_s = str(_get(desc_result, "status", "Status") or "").strip().lower()
    if dc_s == "not_outlier":
        agents["description_coherence"] = {"status": "correct", "confidence": "high"}
    elif dc_s == "outlier":
        agents["description_coherence"] = {"status": "incorrect", "confidence": "high"}
    else:
        # error / skipped / unknown → cannot score
        agents["description_coherence"] = {"status": "na", "confidence": None}

    return agents


def _agent_score(status: str, confidence: Optional[str], is_binary: bool) -> Optional[float]:
    if status == "na":
        return None
    if is_binary:
        return 1.0 if status == "correct" else 0.0
    if status == "correct":
        return CORRECT_MULT.get(confidence or "", None)
    if status == "incorrect":
        return INCORRECT_MULT.get(confidence or "", None)
    return None


def run_scoring_agent(
    audit_result: Dict,
    isq_result: Dict,
    desc_result: Dict,
    retail2_result: Dict,
) -> Dict[str, Any]:
    """Compute composite BuyLead score from existing agent results."""
    weights, thresholds = _parse_config()
    inputs = _map_inputs(audit_result, isq_result, desc_result, retail2_result)

    na_keys = [k for k, v in inputs.items() if v["status"] == "na"]
    avail_keys = [k for k in inputs if k not in na_keys]

    w_na = sum(weights.get(k, 0) for k in na_keys)
    w_avail_total = sum(weights.get(k, 0) for k in avail_keys)

    adj_weights: Dict[str, float] = {}
    for k in inputs:
        if k in na_keys:
            adj_weights[k] = 0.0
        else:
            base = weights.get(k, 0)
            adj_weights[k] = base + (base / w_avail_total * w_na if w_avail_total > 0 else 0)

    breakdown: List[Dict[str, Any]] = []
    total_score = 0.0
    total_w = 0.0

    for k in inputs:
        inp = inputs[k]
        is_binary = k in BINARY_KEYS
        sc = _agent_score(inp["status"], inp["confidence"], is_binary)
        aw = adj_weights[k]

        if inp["status"] != "na":
            if sc is not None:
                total_score += sc * aw
            total_w += aw

        breakdown.append({
            "key": k,
            "display_name": _DISPLAY_NAMES.get(k, k),
            "base_weight": weights.get(k, 0),
            "adjusted_weight": round(aw, 2),
            "is_na": k in na_keys,
            "is_binary": is_binary,
            "status": inp["status"],
            "confidence": inp["confidence"],
            "agent_score": round(sc, 3) if sc is not None else None,
            "contribution": round(sc * aw, 2) if sc is not None else 0.0,
        })

    composite = round((total_score / total_w) * 100) if total_w > 0 else 0

    approved_threshold = thresholds.get("approved", 75)
    reject_threshold = thresholds.get("reject", 30)

    if composite >= approved_threshold:
        verdict = "Approved"
        verdict_class = "approved"
    elif composite < reject_threshold:
        verdict = "Do Not Approve"
        verdict_class = "reject"
    else:
        verdict = "Needs Review"
        verdict_class = "review"

    return {
        "composite_score": composite,
        "verdict": verdict,
        "verdict_class": verdict_class,
        "agent_breakdown": breakdown,
        "na_agents": na_keys,
        "redistributed_weight": round(w_na, 2),
    }


_BINARY_KEYS_A2: set = set()  # Agent 2 agents all return Confidence — use confidence-weighted scoring

_DISPLAY_NAMES_A2 = {
    "specs_vs_category": "Specs vs Category (A2)",
    "title_vs_category": "Title vs Category (A2)",
    "title_vs_specs": "Title vs Specs (A2)",
    "isq_validation": "ISQ Validation",
    "retail_classification": "Retail Classification",
    "description_coherence": "Description Coherence",
}


def _map_inputs_agent2(
    specs_vs_cat_a2_result: Dict,
    title_vs_cat_a2_result: Dict,
    title_vs_specs_a2_result: Dict,
    isq_result: Dict,
    desc_result: Dict,
    retail2_result: Dict,
) -> Dict[str, Dict[str, Any]]:
    """Map agent2 results + shared agents to scoring inputs."""
    agents: Dict[str, Dict[str, Any]] = {}

    def _map_conf(result: Dict, key: str) -> None:
        s = (_get(result, "Status", "status") or "").strip()
        c = _conf_norm(_get(result, "Confidence", "confidence"))
        if s == "Correct":
            agents[key] = {"status": "correct", "confidence": c or "high"}
        elif s == "Incorrect":
            agents[key] = {"status": "incorrect", "confidence": c}
        else:
            agents[key] = {"status": "na", "confidence": None}

    _map_conf(specs_vs_cat_a2_result, "specs_vs_category")
    _map_conf(title_vs_cat_a2_result, "title_vs_category")
    _map_conf(title_vs_specs_a2_result, "title_vs_specs")

    # Shared agents — identical logic to _map_inputs
    isq_s = _get(isq_result, "status")
    if isq_s == "not_outlier":
        agents["isq_validation"] = {"status": "correct", "confidence": "high"}
    elif isq_s == "outlier":
        agents["isq_validation"] = {"status": "incorrect", "confidence": "high"}
    else:
        agents["isq_validation"] = {"status": "na", "confidence": None}

    rc_cls = _get(retail2_result, "Classification", "classification")
    rc_c = _conf_norm(_get(retail2_result, "Confidence", "confidence"))
    if rc_cls in ("Retail", "Non-Retail", "RETAIL", "NON-RETAIL"):
        agents["retail_classification"] = {"status": "correct", "confidence": rc_c or "high"}
    elif rc_cls in ("UNCLASSIFIED", ""):
        agents["retail_classification"] = {"status": "na", "confidence": None}
    else:
        agents["retail_classification"] = {"status": "incorrect", "confidence": rc_c}

    # Binary description agent: not_outlier→1.0, outlier→0.0 (confidence pinned "high").
    dc_s = str(_get(desc_result, "status", "Status") or "").strip().lower()
    if dc_s == "not_outlier":
        agents["description_coherence"] = {"status": "correct", "confidence": "high"}
    elif dc_s == "outlier":
        agents["description_coherence"] = {"status": "incorrect", "confidence": "high"}
    else:
        agents["description_coherence"] = {"status": "na", "confidence": None}

    return agents


def run_scoring_agent2(
    specs_vs_cat_a2_result: Dict,
    title_vs_cat_a2_result: Dict,
    title_vs_specs_a2_result: Dict,
    isq_result: Dict,
    desc_result: Dict,
    retail2_result: Dict,
) -> Dict[str, Any]:
    """Composite score using Agent 2 category checks + shared agents. Reads same weights."""
    weights, thresholds = _parse_config()
    inputs = _map_inputs_agent2(
        specs_vs_cat_a2_result, title_vs_cat_a2_result, title_vs_specs_a2_result,
        isq_result, desc_result, retail2_result,
    )

    na_keys = [k for k, v in inputs.items() if v["status"] == "na"]
    avail_keys = [k for k in inputs if k not in na_keys]

    w_na = sum(weights.get(k, 0) for k in na_keys)
    w_avail_total = sum(weights.get(k, 0) for k in avail_keys)

    adj_weights: Dict[str, float] = {}
    for k in inputs:
        if k in na_keys:
            adj_weights[k] = 0.0
        else:
            base = weights.get(k, 0)
            adj_weights[k] = base + (base / w_avail_total * w_na if w_avail_total > 0 else 0)

    breakdown: List[Dict[str, Any]] = []
    total_score = 0.0
    total_w = 0.0

    for k in inputs:
        inp = inputs[k]
        is_binary = k in _BINARY_KEYS_A2
        sc = _agent_score(inp["status"], inp["confidence"], is_binary)
        aw = adj_weights[k]

        if inp["status"] != "na":
            if sc is not None:
                total_score += sc * aw
            total_w += aw

        breakdown.append({
            "key": k,
            "display_name": _DISPLAY_NAMES_A2.get(k, k),
            "base_weight": weights.get(k, 0),
            "adjusted_weight": round(aw, 2),
            "is_na": k in na_keys,
            "is_binary": is_binary,
            "status": inp["status"],
            "confidence": inp["confidence"],
            "agent_score": round(sc, 3) if sc is not None else None,
            "contribution": round(sc * aw, 2) if sc is not None else 0.0,
        })

    composite = round((total_score / total_w) * 100) if total_w > 0 else 0

    approved_threshold = thresholds.get("approved", 75)
    reject_threshold = thresholds.get("reject", 30)

    if composite >= approved_threshold:
        verdict = "Approved"
        verdict_class = "approved"
    elif composite < reject_threshold:
        verdict = "Do Not Approve"
        verdict_class = "reject"
    else:
        verdict = "Needs Review"
        verdict_class = "review"

    return {
        "composite_score": composite,
        "verdict": verdict,
        "verdict_class": verdict_class,
        "agent_breakdown": breakdown,
        "na_agents": na_keys,
        "redistributed_weight": round(w_na, 2),
    }
