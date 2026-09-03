# LangGraph Migration — ISQ Validation & Description Agents

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `isq_validation_agent` and `description_agent` from the custom `requests`-based `auditor_llm.py` pattern to the LangGraph `StateGraph` pattern already used by the other 6 LLM agents, with zero behavioural or interface changes.

**Architecture:** Each agent's `agent.py` is replaced with a two-node `StateGraph` (`prepare_input` → `classify`), matching exactly the pattern in `specs_vs_category_agent2/agent.py`. The public function signatures, return-dict shapes, prompt-override keys, `__init__.py` exports, and `asyncio.gather()` call in the router all stay identical. The `auditor_llm.py` files become dead code and are deleted.

**Tech Stack:** Python 3.12+, LangGraph 0.2.x (pinned), LangChain `langchain_openai.ChatOpenAI`, FastAPI, asyncio.

---

## Pre-flight: What must NOT change

These are the contracts the router (`app/routers/audit.py`) depends on — verify all of them still hold after each task:

| Contract | Where checked |
|---|---|
| `run_isq_validation_agent(offer_id, buylead_response, _trace=True)` returns `{result, agent_input, raw_output, system_prompt, user_message}` | `audit.py:560–588` |
| `run_description_agent(offer_id, buylead_response, _trace=True)` returns same shape | `audit.py:591–619` |
| Both are `async def` — called inside `asyncio.gather()` | `audit.py:410–421` |
| `isq_result` keys: `Status`, `Score`, `Confidence`, `Reason`, `Issues`, `Display_id` | `audit.py:576–587`, `batch_stream`, `scoring_agent` |
| `desc_result` keys: `Status`, `Score`, `Confidence`, `Reason`, `Issues`, `Display_id` | `audit.py:607–618`, `batch_stream`, `scoring_agent` |
| `get_active_prompt("isq_validation")` key — unchanged in `prompt_override_service.py` | `prompt_override_service.py:52` |
| `get_active_prompt("description")` key — unchanged | `prompt_override_service.py:57` |
| `__init__.py` exports unchanged | `app/isq_validation_agent/__init__.py`, `app/description_agent/__init__.py` |

---

## File Map

| File | Action | Reason |
|---|---|---|
| `app/isq_validation_agent/agent.py` | **Rewrite** | Replace `asyncio.to_thread(ISQValidatorLLM)` with LangGraph `StateGraph` |
| `app/isq_validation_agent/auditor_llm.py` | **Delete** | Dead code after migration |
| `app/description_agent/agent.py` | **Rewrite** | Replace `asyncio.to_thread(DescriptionValidatorLLM)` with LangGraph `StateGraph` |
| `app/description_agent/auditor_llm.py` | **Delete** | Dead code after migration |
| `app/isq_validation_agent/__init__.py` | **No change** | Already exports `run_isq_validation_agent` from `.agent` |
| `app/description_agent/__init__.py` | **No change** | Already exports `run_description_agent` from `.agent` |
| `app/routers/audit.py` | **No change** | Imports only through `__init__.py`; return shape is identical |
| `app/services/prompt_override_service.py` | **No change** | Keys `isq_validation` and `description` already registered |

---

## Task 1: Migrate `isq_validation_agent/agent.py` to LangGraph

**Files:**
- Modify: `app/isq_validation_agent/agent.py` (full rewrite)

### Background

Current flow:
1. `run_isq_validation_agent` calls `_extract_inputs()` inline
2. Early-exits if no ISQs
3. Calls `ISQValidatorLLM().validate_isq()` wrapped in `asyncio.to_thread()` (sync HTTP)
4. Maps `ISQAuditResult` dataclass fields to the result dict

New flow:
1. `_prepare_input` node: extract inputs → `state["agent_input"]`
2. `_classify` node: early-exit if no ISQs, else call `ChatOpenAI.ainvoke()` with retry loop
3. `run_isq_validation_agent` invokes compiled graph, returns trace dict or result dict

Key mappings to preserve (LLM field → result dict key):
- LLM `"status"` → `"Status"` (capitalised, to match router expectations)
- LLM `"score"` → `"Score"` (via `_safe_score`)
- LLM `"confidence"` → `"Confidence"`
- LLM `"reasoning"` or `"reason"` → `"Reason"` (prompt may use either)
- LLM `"issues"` → `"Issues"` (via `_safe_issues`)

- [ ] **Step 1.1: Verify current ISQ agent test baseline**

Check what the current `agent.py` imports from `auditor_llm.py` so you know exactly what's being replaced:

```bash
grep -n "from .auditor_llm import\|from auditor_llm import" app/isq_validation_agent/agent.py
```

Expected output:
```
26:from .auditor_llm import (
27:    DEFAULT_SYSTEM_PROMPT,
28:    ISQValidatorLLM,
29:    build_user_message,
30:)
```

- [ ] **Step 1.2: Verify LangGraph is importable in this environment**

```bash
python -c "from langgraph.graph import END, StateGraph; print('ok')"
```

Expected output: `ok`

If this fails, do NOT proceed — LangGraph is not installed or the wrong version is active.

- [ ] **Step 1.3: Write the new `agent.py`**

Replace `app/isq_validation_agent/agent.py` entirely with:

```python
"""ISQ Validation Agent — async LangGraph agent.

Reads the buylead's ISQ list and asks the LLM to classify internal coherence.

LLM output contract (from prompt.md):
    {
        "status": "Correct" | "Incorrect" | "Not Available" | "Error",
        "score": float (0.0–1.0),
        "confidence": "High" | "Medium" | "Low",
        "reasoning": str,
        "issues": [{"type": str, "fields": [str], "detail": str}]
    }
"""
import asyncio
import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from app.services.buylead_service import parse_enrichmentinfo_to_isq

log = logging.getLogger("bl-auditor.isq_validation_agent")
_LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))


class ISQValidationState(TypedDict, total=False):
    offer_id: str
    buylead_response: Dict[str, Any]
    agent_input: Dict[str, Any]
    system_prompt: str
    user_message: str
    raw_output: str
    result: Dict[str, Any]


def _build_isq_table(isq_list: List[Dict[str, str]]) -> str:
    if not isq_list:
        return "(none)"
    rows = ["| Spec | Value |", "| --- | --- |"]
    for item in isq_list:
        spec = str(item.get("IM_SPEC_MASTER_DESC", "")).strip() or "(unnamed)"
        value = str(item.get("ISQ_RESPONSE", "")).strip() or "(empty)"
        rows.append(f"| {spec.replace('|', '/')} | {value.replace('|', '/')} |")
    return "\n".join(rows)


def _read_prompt() -> str:
    from app.services.prompt_override_service import get_active_prompt
    return get_active_prompt("isq_validation")[0]


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


def _safe_score(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num > 1.0:
        num = num / 100.0
    if num < 0.0:
        num = 0.0
    if num > 1.0:
        num = 1.0
    return round(num, 2)


def _safe_issues(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            out.append({
                "type": str(item.get("type", "")),
                "fields": item.get("fields") if isinstance(item.get("fields"), list) else [],
                "detail": str(item.get("detail", "")),
            })
    return out


def _build_user_message(agent_input: Dict[str, Any]) -> str:
    return (
        "Audit the following BuyLead's ISQ (In-Specification Questions) for internal coherence.\n\n"
        f"MCAT: {agent_input.get('mcat_name', '')}\n"
        f"Item: {agent_input.get('item_name', '')}\n\n"
        f"ISQs:\n{agent_input.get('isq_table', '(none)')}\n\n"
        "Return ONLY the JSON object described in the system prompt. No prose, no markdown fences."
    )


async def _prepare_input(state: ISQValidationState) -> ISQValidationState:
    data = (state["buylead_response"] or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    mcat_name = str(data.get("PRIME_MCAT_NAME") or "Unknown")
    item_name = str(data.get("ETO_OFR_TITLE") or "")
    offer_display_id = data.get("ETO_OFR_DISPLAY_ID") or state["offer_id"]
    isq_list = parse_enrichmentinfo_to_isq(data.get("ENRICHMENTINFO"))
    isq_table = _build_isq_table(isq_list)
    return {
        "agent_input": {
            "Display_id": offer_display_id,
            "mcat_name": mcat_name,
            "item_name": item_name,
            "isq_list": isq_list,
            "isq_table": isq_table,
            "isq_count": len(isq_list),
        }
    }


async def _classify(state: ISQValidationState) -> ISQValidationState:
    base_url = os.getenv("LLM_BASE_URL")
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    timeout = float(os.getenv("LLM_TIMEOUT", "60"))

    if not api_key or not model:
        raise RuntimeError("Missing LLM_API_KEY or LLM_MODEL")

    agent_input = state["agent_input"]

    if agent_input.get("isq_count", 0) == 0:
        result = {
            "Display_id": agent_input.get("Display_id", state.get("offer_id", "")),
            "Status": "Missing",
            "Score": 0.0,
            "Confidence": "High",
            "Reason": "No ISQs were provided in the BuyLead.",
            "Issues": [{"type": "missing", "fields": ["ALL"], "detail": "ISQ list is empty."}],
        }
        return {
            "system_prompt": "(skipped — no ISQs)",
            "user_message": _build_user_message(agent_input),
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
                "isq_validation_agent attempt %d/%d failed: %s: %s",
                attempt, _LLM_MAX_RETRIES + 1, type(exc).__name__, exc,
            )
            if attempt <= _LLM_MAX_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))
    else:
        raise RuntimeError(
            f"isq_validation_agent exhausted {_LLM_MAX_RETRIES + 1} attempts: {last_exc}"
        ) from last_exc

    raw_output = str(response.content)
    try:
        parsed = _clean_output(raw_output)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"isq_validation_agent parse failed: {exc}. Raw: {raw_output[:500]!r}"
        ) from exc

    result = {
        "Display_id": agent_input.get("Display_id", ""),
        "Status": str(parsed.get("status", "Error")),
        "Score": _safe_score(parsed.get("score")),
        "Confidence": str(parsed.get("confidence", "Low")),
        "Reason": str(parsed.get("reasoning", parsed.get("reason", "Error parsing reasoning"))),
        "Issues": _safe_issues(parsed.get("issues")),
    }

    return {
        "system_prompt": system_prompt,
        "user_message": user_message,
        "raw_output": raw_output,
        "result": result,
    }


def _build_graph():
    graph = StateGraph(ISQValidationState)
    graph.add_node("prepare_input", _prepare_input)
    graph.add_node("classify", _classify)
    graph.set_entry_point("prepare_input")
    graph.add_edge("prepare_input", "classify")
    graph.add_edge("classify", END)
    return graph.compile()


@lru_cache(maxsize=1)
def _compiled_graph():
    return _build_graph()


async def run_isq_validation_agent(
    offer_id: str,
    buylead_response: Dict[str, Any],
    _trace: bool = False,
) -> Dict[str, Any]:
    state = await _compiled_graph().ainvoke(
        {"offer_id": offer_id, "buylead_response": buylead_response}
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
```

- [ ] **Step 1.4: Verify the module imports cleanly**

```bash
python -c "from app.isq_validation_agent import run_isq_validation_agent; print('ok')"
```

Expected output: `ok`

If you see `ImportError` referencing `auditor_llm`, you haven't fully replaced the file — check step 1.3.
If you see `ImportError` referencing `langgraph`, the environment doesn't have LangGraph — stop.

- [ ] **Step 1.5: Verify graph compiles correctly**

```bash
python -c "
from app.isq_validation_agent.agent import _compiled_graph
g = _compiled_graph()
print('nodes:', list(g.nodes))
print('ok')
"
```

Expected output (order may vary):
```
nodes: ['prepare_input', 'classify', '__start__', '__end__']
ok
```

- [ ] **Step 1.6: Commit**

```bash
git add app/isq_validation_agent/agent.py
git commit -m "feat: migrate isq_validation_agent to LangGraph StateGraph pattern"
```

---

## Task 2: Migrate `description_agent/agent.py` to LangGraph

**Files:**
- Modify: `app/description_agent/agent.py` (full rewrite)

### Background

Identical structural migration to Task 1. Key differences:
- Extracts `ETO_OFR_DESC` instead of `ENRICHMENTINFO`
- Early-exit condition is empty description string (not empty ISQ list)
- Early-exit `Status` is `"No Description"` (not `"Missing"`)
- No `_build_isq_table` needed
- `prompt_override_service` key is `"description"` (not `"isq_validation"`)

- [ ] **Step 2.1: Write the new `description_agent/agent.py`**

Replace `app/description_agent/agent.py` entirely with:

```python
"""Description Agent — async LangGraph agent.

Reads the buylead's MCAT, item title, and free-text description, and asks the
LLM to classify whether the description is coherent with the title and MCAT.

LLM output contract (from prompt.md):
    {
        "status": "Correct" | "Incorrect" | "Not Available" | "Error",
        "score": float (0.05–1.0),
        "confidence": "High" | "Medium" | "Low",
        "reasoning": str,
        "issues": [{"type": str, "fields": [str], "detail": str}]
    }
"""
import asyncio
import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, TypedDict

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


def _safe_score(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num > 1.0:
        num = num / 100.0
    if num < 0.0:
        num = 0.0
    if num > 1.0:
        num = 1.0
    return round(num, 2)


def _safe_issues(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            out.append({
                "type": str(item.get("type", "")),
                "fields": item.get("fields") if isinstance(item.get("fields"), list) else [],
                "detail": str(item.get("detail", "")),
            })
    return out


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
            "Display_id": agent_input.get("Display_id", state.get("offer_id", "")),
            "Status": "No Description",
            "Score": 0.0,
            "Confidence": "High",
            "Reason": "No description was provided in the BuyLead.",
            "Issues": [{"type": "missing", "fields": ["description"], "detail": "Description is empty."}],
        }
        return {
            "system_prompt": "(skipped — no description)",
            "user_message": _build_user_message(agent_input),
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

    result = {
        "Display_id": agent_input.get("Display_id", ""),
        "Status": str(parsed.get("status", "Error")),
        "Score": _safe_score(parsed.get("score")),
        "Confidence": str(parsed.get("confidence", "Low")),
        "Reason": str(parsed.get("reasoning", parsed.get("reason", "Error parsing reasoning"))),
        "Issues": _safe_issues(parsed.get("issues")),
    }

    return {
        "system_prompt": system_prompt,
        "user_message": user_message,
        "raw_output": raw_output,
        "result": result,
    }


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
    state = await _compiled_graph().ainvoke(
        {"offer_id": offer_id, "buylead_response": buylead_response}
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
```

- [ ] **Step 2.2: Verify the module imports cleanly**

```bash
python -c "from app.description_agent import run_description_agent; print('ok')"
```

Expected output: `ok`

- [ ] **Step 2.3: Verify graph compiles correctly**

```bash
python -c "
from app.description_agent.agent import _compiled_graph
g = _compiled_graph()
print('nodes:', list(g.nodes))
print('ok')
"
```

Expected output (order may vary):
```
nodes: ['prepare_input', 'classify', '__start__', '__end__']
ok
```

- [ ] **Step 2.4: Verify the router still imports everything without errors**

```bash
python -c "
import app.routers.audit
print('router import ok')
"
```

Expected output: `router import ok`

This catches any accidental name collision or import-time failure.

- [ ] **Step 2.5: Commit**

```bash
git add app/description_agent/agent.py
git commit -m "feat: migrate description_agent to LangGraph StateGraph pattern"
```

---

## Task 3: Delete dead `auditor_llm.py` files

**Files:**
- Delete: `app/isq_validation_agent/auditor_llm.py`
- Delete: `app/description_agent/auditor_llm.py`

After Tasks 1 and 2, these files are unreferenced. Nothing imports them — confirm before deleting.

- [ ] **Step 3.1: Confirm nothing still imports from `auditor_llm`**

```bash
grep -r "auditor_llm" app/ --include="*.py"
```

Expected output: **empty** (no matches). If any file still references `auditor_llm`, fix that import before deleting.

- [ ] **Step 3.2: Delete both files**

```bash
rm app/isq_validation_agent/auditor_llm.py
rm app/description_agent/auditor_llm.py
```

- [ ] **Step 3.3: Verify imports still work after deletion**

```bash
python -c "
from app.isq_validation_agent import run_isq_validation_agent
from app.description_agent import run_description_agent
import app.routers.audit
print('all imports ok')
"
```

Expected output: `all imports ok`

- [ ] **Step 3.4: Commit**

```bash
git add -u app/isq_validation_agent/auditor_llm.py app/description_agent/auditor_llm.py
git commit -m "chore: delete dead auditor_llm.py files — replaced by LangGraph agents"
```

---

## Task 4: Smoke Test — End-to-end verification

No automated test suite exists in this project. These manual steps verify the full pipeline still works.

- [ ] **Step 4.1: Start the server**

```bash
uvicorn main:app --reload --port 8080
```

Watch startup output for any `ImportError` or `ModuleNotFoundError`. The server must reach `Application startup complete`.

- [ ] **Step 4.2: Run a single audit through the UI**

Open `http://localhost:8080` in a browser. Submit a known offer ID (e.g. `142764424452`).

Verify in the result page:
- **ISQ Validation** section shows a Status (not blank, not `"Error"` with a traceback)
- **Description Agent** section shows a Status (not blank, not `"Error"` with a traceback)
- Both sections show a Score (float or `null` for missing-data cases)
- The Trace shows both agents ran with `system_prompt` and `user_message` populated

- [ ] **Step 4.3: Check the trace detail view**

From the result page, click the trace ID link. Verify:
- `ISQ Validation Agent` step shows `system_prompt`, `user_message`, `raw_output`, `parsed` — all populated
- `Description Agent` step shows the same
- Both step statuses are `"ok"` (not `"error"`)

- [ ] **Step 4.4: Run a batch of 2–3 offers**

Go to `http://localhost:8080/batch`. Submit 2–3 offer IDs comma-separated.

Verify:
- `isq_status` and `desc_status` columns are populated in the stream results
- `isq_score` and `desc_score` are numeric (not empty strings due to serialization issues)
- No 500 errors in the stream

- [ ] **Step 4.5: Verify the prompt override still works for ISQ agent**

Go to `http://localhost:8080/prompts`. Find "ISQ Validation Agent". The current bundled prompt should be shown. Append a space to it and save. Re-run an audit. The ISQ agent must still produce a verdict (confirming `get_active_prompt("isq_validation")` still resolves). Reset the prompt afterward.

- [ ] **Step 4.6: Verify the prompt override still works for Description agent**

Same as 4.5 but for "Description Agent" / `get_active_prompt("description")`.

- [ ] **Step 4.7: Final commit (if any fixes were needed during smoke test)**

```bash
git add -A
git commit -m "fix: smoke test corrections for LangGraph ISQ/description migration"
```

If no fixes were needed, skip this step.

---

## Self-Review Checklist

- [x] **Spec coverage:** Both agents migrated. `auditor_llm.py` deleted. `asyncio.gather()` unchanged. Router unchanged. `__init__.py` unchanged.
- [x] **No placeholders:** All code blocks are complete and runnable.
- [x] **Type consistency:** `ISQValidationState` and `DescriptionAgentState` both use same field names (`offer_id`, `buylead_response`, `agent_input`, `system_prompt`, `user_message`, `raw_output`, `result`) as the 6 existing LangGraph agents.
- [x] **Result dict keys preserved:** `Status`, `Score`, `Confidence`, `Reason`, `Issues`, `Display_id` — identical to what `audit.py` reads at lines 560–619.
- [x] **Early-exit preserved:** No-ISQ → `Status: "Missing"` (was `"Missing"` before). Empty description → `Status: "No Description"` (was `"No Description"` before).
- [x] **Prompt keys preserved:** `"isq_validation"` and `"description"` — same as in `prompt_override_service.py`.
- [x] **`_safe_score` logic preserved:** Legacy 0–100 integer scores divided by 100; clamped to [0.0, 1.0].
- [x] **`_safe_issues` logic preserved:** Filters non-dict items; normalises `type`, `fields`, `detail` keys.
- [x] **`_trace=False` path preserved:** Returns `state["result"]` directly (same as before).
- [x] **Parallel execution preserved:** Both functions are `async def` and invoked via `asyncio.gather()` in the router — no change needed there.
- [x] **`reasoning` vs `reason` ambiguity handled:** `parsed.get("reasoning", parsed.get("reason", "Error parsing reasoning"))` covers both field names the LLM might return.
