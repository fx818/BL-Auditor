"""Description Agent — async LangGraph agent.

Reads the buylead's MCAT, item title, and free-text description, and asks the
LLM to classify whether the description is coherent with the title and MCAT.

LLM output contract (from prompt.md):
    {
        "status": "outlier" | "not_outlier",
        "reason": str
    }
"""
import asyncio
import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

log = logging.getLogger("bl-auditor.description_agent")
_LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))


class DescriptionAgentState(TypedDict, total=False):
    offer_id: str
    buylead_response: Dict[str, Any]
    agent_input: Dict[str, Any]
    system_prompt: str
    user_message: str
    raw_output: str
    result: Dict[str, Any]


def _read_prompt() -> str:
    from app.services.prompt_override_service import get_active_prompt
    return get_active_prompt("description")[0]


def _clean_output(raw: str) -> Dict[str, Any]:
    text = (raw or "").replace("```json", "").replace("```", "").strip()
    if not text:
        raise ValueError("LLM returned empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError(f"No JSON in LLM output: {text[:300]}")
        return json.loads(match.group(0))


def _build_user_message(agent_input: Dict[str, Any]) -> str:
    description = agent_input.get("description") or "(empty)"
    return (
        "Audit the following BuyLead's description against its title and MCAT.\n\n"
        f"MCAT: {agent_input.get('mcat_name', '')}\n"
        f"Item: {agent_input.get('item_name', '')}\n"
        f"Description:\n{description}\n\n"
        "Return ONLY the JSON object described in the system prompt. No prose, no markdown fences."
    )


async def _prepare_input(state: DescriptionAgentState) -> DescriptionAgentState:
    data = (state["buylead_response"] or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    mcat_name = str(data.get("PRIME_MCAT_NAME") or "Unknown")
    item_name = str(data.get("ETO_OFR_TITLE") or "")
    description = str(data.get("ETO_OFR_DESC") or "")
    offer_display_id = data.get("ETO_OFR_DISPLAY_ID") or state["offer_id"]
    return {
        "agent_input": {
            "Display_id": offer_display_id,
            "mcat_name": mcat_name,
            "item_name": item_name,
            "description": description,
        }
    }


async def _classify(state: DescriptionAgentState) -> DescriptionAgentState:
    base_url = os.getenv("LLM_BASE_URL")
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    timeout = float(os.getenv("LLM_TIMEOUT", "60"))

    if not api_key or not model:
        raise RuntimeError("Missing LLM_API_KEY or LLM_MODEL")

    agent_input = state["agent_input"]

    if not agent_input.get("description", "").strip():
        result = {
            "status": "not_outlier",
            "reason": "No description present.",
        }
        return {
            "system_prompt": "(skipped — Not Available)",
            "user_message": "(skipped — Not Available)",
            "raw_output": "",
            "result": result,
        }

    system_prompt = _read_prompt()
    user_message = _build_user_message(agent_input)

    last_exc: Exception | None = None
    response = None
    for attempt in range(1, _LLM_MAX_RETRIES + 2):
        try:
            try:
                llm = ChatOpenAI(
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                    timeout=timeout,
                    model_kwargs={
                        "response_format": {"type": "json_object"},
                        "reasoning_effort": "minimal",
                    },
                    extra_body={
                        "google": {"thinking_config": {"thinking_budget": 0}},
                    },
                )
                response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_message)])
            except Exception:
                llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url, timeout=timeout)
                response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_message)])
            break
        except Exception as exc:
            last_exc = exc
            log.warning(
                "description_agent attempt %d/%d failed: %s: %s",
                attempt, _LLM_MAX_RETRIES + 1, type(exc).__name__, exc,
            )
            if attempt <= _LLM_MAX_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))
    else:
        raise RuntimeError(
            f"description_agent exhausted {_LLM_MAX_RETRIES + 1} attempts: {last_exc}"
        ) from last_exc

    raw_output = str(response.content)
    try:
        parsed = _clean_output(raw_output)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"description_agent parse failed: {exc}. Raw: {raw_output[:500]!r}"
        ) from exc

    # Explicitly map and normalise to the strict {status, reason} contract.
    status = str(parsed.get("status", "not_outlier")).strip().lower()
    if status not in ("outlier", "not_outlier"):
        status = "not_outlier"
    result = {
        "status": status,
        "reason": str(parsed.get("reason", parsed.get("reasoning", ""))),  # accepts both field names
    }

    return {
        "system_prompt": system_prompt,
        "user_message": user_message,
        "raw_output": raw_output,
        "result": result,
    }


# Buyer-activity ids whose `keyword` is a usable search/browse/enquiry signal:
# 1282 Search, 559 Browse, 548, 4231 enquiry-intent.
_ACTIVITY_KEYWORD_IDS = {1282, 559, 548, 4231}


def _collect_activity_keywords(buyer_activity: Dict[str, Any]) -> List[str]:
    """Distinct decoded keywords (first-seen order) from buyer-activity events
    whose fk_activity_id is in _ACTIVITY_KEYWORD_IDS."""
    seen: set = set()
    out: List[str] = []
    for ev in (buyer_activity or {}).get("events") or []:
        if ev.get("activity_id") in _ACTIVITY_KEYWORD_IDS:
            kw = str(ev.get("keyword") or "").strip()
            if kw and kw.lower() not in seen:
                seen.add(kw.lower())
                out.append(kw)
    return out


# Tiny stopword set dropped when token-matching searched terms against the
# activity log. Kept small on purpose — over-stripping causes false matches.
_TERM_STOPWORDS = {"a", "an", "the", "and", "or", "for", "of", "to", "with", "in"}


def _term_tokens(text: str) -> set:
    """Significant tokens of `text`: lowercased, '-' treated as a space,
    non-alphanumerics dropped, tiny stopwords removed."""
    text = (text or "").lower().replace("-", " ")
    return {t for t in re.findall(r"[a-z0-9]+", text) if t not in _TERM_STOPWORDS}


def parse_searched_terms(description: str) -> List[str]:
    """Split a "Buyer searched for X, Y, and Z" description into raw terms.

    Strips the leading "buyer searched for" phrase, then splits on commas only
    (NOT on '-', which stays inside a term, e.g. "second-hand"). A leading
    "and"/"or" left over from ", and ..." is trimmed for display. Blanks dropped.
    """
    text = (description or "").strip()
    prefix = "buyer searched for"
    if text.lower().startswith(prefix):
        text = text[len(prefix):]
    out: List[str] = []
    for chunk in text.split(","):
        term = re.sub(r"^\s*(?:and|or)\s+", "", chunk.strip(), flags=re.IGNORECASE).strip()
        if term:
            out.append(term)
    return out


def match_searched_terms(description: str, buyer_activity: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministically (no LLM) check whether each term in a "Buyer searched
    for X, Y, Z" description appears in the buyer's activity-log keywords.

    A term matches a log keyword when one's significant token set is a subset of
    the other's (after lowercasing and treating '-' as a space) — so the desc
    term "a second-hand powder coating machine" matches the log keyword
    "powder coating machine". Returns:
        {"terms": [{"term": str, "found": bool, "matched_keyword": str|None}],
         "log_keywords": [str], "found_count": int, "total": int}
    """
    terms = parse_searched_terms(description)
    log_keywords = _collect_activity_keywords(buyer_activity)
    kw_tokens = [(kw, _term_tokens(kw)) for kw in log_keywords]

    results: List[Dict[str, Any]] = []
    for term in terms:
        t_tokens = _term_tokens(term)
        matched = None
        if t_tokens:
            for kw, k_tokens in kw_tokens:
                if k_tokens and (k_tokens <= t_tokens or t_tokens <= k_tokens):
                    matched = kw
                    break
        results.append({"term": term, "found": matched is not None, "matched_keyword": matched})

    return {
        "terms": results,
        "log_keywords": log_keywords,
        "found_count": sum(1 for r in results if r["found"]),
        "total": len(results),
    }


async def select_activity_keywords(
    mcat_name: str, buyer_activity: Dict[str, Any], max_keywords: int = 4
) -> Dict[str, Any]:
    """Pick the top keywords (≤ max_keywords) usable for the BL description.

    Candidates = distinct keywords from buyer-activity ids 1282/559/548/4231.
    The LLM ranks them by relevance to the MCAT and returns the best few, in
    order. Returns:
        {"keywords": [str], "candidates": [str], "reason": str, "error": str|None}
    On any LLM/parse failure it falls back to the first `max_keywords` candidates.
    """
    candidates = _collect_activity_keywords(buyer_activity)
    if not candidates:
        return {"keywords": [], "candidates": [], "reason": "No keywords in buyer activity.", "error": None}

    base_url = os.getenv("LLM_BASE_URL")
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    timeout = float(os.getenv("LLM_TIMEOUT", "60"))
    if not api_key or not model:
        return {"keywords": candidates[:max_keywords], "candidates": candidates,
                "reason": "LLM not configured; used first candidates.", "error": "Missing LLM_API_KEY or LLM_MODEL"}

    system_prompt = (
        "You select search keywords that best describe what a buyer wants, for use "
        "as a BuyLead product description. You are given the buyer's product category "
        "(MCAT) and a list of keywords the buyer actually searched/browsed/enquired. "
        f"Return the up to {max_keywords} keywords MOST relevant to the MCAT, ordered "
        "most-relevant first. Only choose from the given candidates; do not invent keywords. "
        'Respond ONLY as JSON: {"keywords": ["..."], "reason": "..."}.'
    )
    user_message = (
        f"MCAT: {mcat_name or 'Unknown'}\n"
        f"Candidate keywords (buyer activity): {json.dumps(candidates, ensure_ascii=False)}\n\n"
        f"Pick up to {max_keywords}, most relevant to the MCAT first. JSON only."
    )

    last_exc: Exception | None = None
    for attempt in range(1, _LLM_MAX_RETRIES + 2):
        try:
            try:
                llm = ChatOpenAI(
                    model=model, api_key=api_key, base_url=base_url, timeout=timeout,
                    model_kwargs={"response_format": {"type": "json_object"}, "reasoning_effort": "minimal"},
                    extra_body={"google": {"thinking_config": {"thinking_budget": 0}}},
                )
                response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_message)])
            except Exception:
                llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url, timeout=timeout)
                response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_message)])
            parsed = _clean_output(str(response.content))
            picked = parsed.get("keywords") if isinstance(parsed, dict) else None
            if not isinstance(picked, list):
                raise ValueError("LLM did not return a 'keywords' list")
            # keep only real candidates (case-insensitive), preserve LLM order, cap at max
            cand_by_lower = {c.lower(): c for c in candidates}
            keywords: List[str] = []
            for k in picked:
                c = cand_by_lower.get(str(k).strip().lower())
                if c and c not in keywords:
                    keywords.append(c)
                if len(keywords) >= max_keywords:
                    break
            if not keywords:
                keywords = candidates[:max_keywords]
            return {"keywords": keywords, "candidates": candidates,
                    "reason": str(parsed.get("reason", "")), "error": None}
        except Exception as exc:
            last_exc = exc
            log.warning("select_activity_keywords attempt %d/%d failed: %s",
                        attempt, _LLM_MAX_RETRIES + 1, exc)
            if attempt <= _LLM_MAX_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))

    return {"keywords": candidates[:max_keywords], "candidates": candidates,
            "reason": "LLM selection failed; used first candidates.", "error": str(last_exc)}


def _build_graph():
    graph = StateGraph(DescriptionAgentState)
    graph.add_node("prepare_input", _prepare_input)
    graph.add_node("classify", _classify)
    graph.set_entry_point("prepare_input")
    graph.add_edge("prepare_input", "classify")
    graph.add_edge("classify", END)
    return graph.compile()


@lru_cache(maxsize=1)
def _compiled_graph():
    return _build_graph()


async def run_description_agent(
    offer_id: str,
    buylead_response: Dict[str, Any],
    _trace: bool = False,
) -> Dict[str, Any]:
    from app.services.langfuse_service import get_langfuse_handler
    _h = get_langfuse_handler()
    _cfg = {"callbacks": [_h], "run_name": f"description_agent:{offer_id}"} if _h else {}
    state = await _compiled_graph().ainvoke(
        {"offer_id": offer_id, "buylead_response": buylead_response},
        config=_cfg,
    )
    if _trace:
        return {
            "result": state["result"],
            "agent_input": state.get("agent_input", {}),
            "raw_output": state.get("raw_output", ""),
            "system_prompt": state.get("system_prompt", ""),
            "user_message": state.get("user_message", ""),
        }
    return state["result"]
