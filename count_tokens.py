"""
Agent-wise token usage counter for a single offer.
Usage: python count_tokens.py [offer_id]
Default offer: 147387228485

Patches ChatOpenAI.ainvoke (LangGraph agents) and requests.post (retail_agent_2)
to intercept token counts from the LLM response before running each agent.
"""
import asyncio
import contextvars
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).parent))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Token store ────────────────────────────────────────────────────────────────
_token_store: Dict[str, Dict[str, int]] = {}
_current_agent: contextvars.ContextVar[str] = contextvars.ContextVar("_current_agent", default="unknown")


def _record(name: str, input_tok: int, output_tok: int) -> None:
    if name not in _token_store:
        _token_store[name] = {"input": 0, "output": 0}
    _token_store[name]["input"] += input_tok
    _token_store[name]["output"] += output_tok


# ── Patch 1: ChatOpenAI.ainvoke (all LangGraph agents) ────────────────────────
from langchain_openai import ChatOpenAI as _ChatOpenAI  # noqa: E402

_orig_ainvoke = _ChatOpenAI.ainvoke


async def _patched_ainvoke(self, input, config=None, **kwargs):
    response = await _orig_ainvoke(self, input, config=config, **kwargs)
    name = _current_agent.get()
    usage = getattr(response, "usage_metadata", None) or {}
    _record(name, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    return response


_ChatOpenAI.ainvoke = _patched_ainvoke


# ── Patch 2: requests.post (retail_agent_2 raw HTTP) ──────────────────────────
import requests as _requests  # noqa: E402

_orig_post = _requests.post


def _patched_post(url, **kwargs):
    response = _orig_post(url, **kwargs)
    name = _current_agent.get()
    try:
        data = response.json()
        usage = data.get("usage", {})
        _record(
            name,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )
    except Exception:
        pass
    return response


_requests.post = _patched_post


# ── Agent runner ───────────────────────────────────────────────────────────────
async def run_agent(display_name: str, coro) -> Any:
    tok = _current_agent.set(display_name)
    try:
        return await coro
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        _current_agent.reset(tok)


# ── Main ───────────────────────────────────────────────────────────────────────
async def main():
    offer_id = sys.argv[1] if len(sys.argv) > 1 else "147387228485"

    print(f"Fetching buylead {offer_id} ...")
    from app.services.buylead_service import fetch_buylead_detail
    try:
        buylead_response = await fetch_buylead_detail(offer_id)
    except Exception as exc:
        print(f"ERROR: Failed to fetch buylead: {exc}")
        sys.exit(1)

    # Shared dependencies
    from app.services.buyer_profile_service import fetch_user_detail_for_buylead
    user_detail = await fetch_user_detail_for_buylead(buylead_response)

    # Imports (after patches are in place)
    from app.retail_agent import run_retail_agent
    from app.retail_agent_2 import run_retail_agent_2
    from app.buyer_viewed_agent import run_buyer_viewed_agent
    from app.isq_validation_agent import run_isq_validation_agent
    from app.description_agent import run_description_agent
    from app.specs_vs_category_agent2 import run_specs_vs_category_agent2
    from app.title_vs_category_agent2 import run_title_vs_category_agent2
    from app.title_vs_specs_agent2 import run_title_vs_specs_agent2
    from app.buyer_profile_agent import run_buyer_profile_agent

    # Agents to run — (display_name, coroutine)
    # retail_agent (LangGraph) is the legacy one; retail_agent_2 (HTTP) is what the orchestrator uses.
    agents = [
        ("retail_agent (LangGraph)",  run_retail_agent(offer_id, buylead_response)),
        ("retail_agent_2 (HTTP)",     run_retail_agent_2(offer_id, buylead_response)),
        ("buyer_viewed",              run_buyer_viewed_agent(offer_id, buylead_response)),
        ("isq_validation",            run_isq_validation_agent(offer_id, buylead_response, user_detail=user_detail)),
        ("description",               run_description_agent(offer_id, buylead_response)),
        ("specs_vs_category",         run_specs_vs_category_agent2(offer_id, buylead_response)),
        ("title_vs_category",         run_title_vs_category_agent2(offer_id, buylead_response)),
        ("title_vs_specs",            run_title_vs_specs_agent2(offer_id, buylead_response)),
        ("buyer_profile",             run_buyer_profile_agent(offer_id, buylead_response, user_detail=user_detail)),
    ]

    print(f"Running {len(agents)} agents sequentially ...\n")
    for name, coro in agents:
        print(f"  > {name} ...", end="", flush=True)
        await run_agent(name, coro)
        stats = _token_store.get(name, {"input": 0, "output": 0})
        print(f" done  [in={stats['input']}, out={stats['output']}]")

    # ── Pricing ────────────────────────────────────────────────────────────────
    PRICE_IN_PER_M  = float(os.getenv("PRICE_INPUT_PER_M",  "0.09"))   # USD / 1M input tokens
    PRICE_OUT_PER_M = float(os.getenv("PRICE_OUTPUT_PER_M", "0.18"))   # USD / 1M output tokens
    USD_INR         = float(os.getenv("USD_INR",             "96.0"))   # exchange rate

    def cost_inr(inp: int, out: int) -> float:
        usd = (inp * PRICE_IN_PER_M + out * PRICE_OUT_PER_M) / 1_000_000
        return usd * USD_INR

    # ── Summary table ──────────────────────────────────────────────────────────
    W = 30
    print("\n" + "=" * (W + 46))
    print(f"  Token usage & cost — offer {offer_id}")
    print(f"  Pricing: Rs.{PRICE_IN_PER_M * USD_INR:.4f}/1K input  Rs.{PRICE_OUT_PER_M * USD_INR:.4f}/1K output  (1 USD = Rs.{USD_INR})")
    print("=" * (W + 46))
    print(f"  {'Agent':<{W}} {'Input':>8}  {'Output':>8}  {'Total':>8}  {'Cost (Rs.)':>10}")
    print("  " + "-" * (W + 44))
    total_in = total_out = 0
    for name, _ in agents:
        stats = _token_store.get(name, {"input": 0, "output": 0})
        inp, out = stats["input"], stats["output"]
        total_in += inp
        total_out += out
        rs = cost_inr(inp, out)
        flag = "  ! no data" if inp == 0 and out == 0 else ""
        print(f"  {name:<{W}} {inp:>8}  {out:>8}  {inp+out:>8}  {rs:>9.4f}{flag}")
    print("  " + "-" * (W + 44))
    total_rs = cost_inr(total_in, total_out)
    print(f"  {'TOTAL':<{W}} {total_in:>8}  {total_out:>8}  {total_in+total_out:>8}  {total_rs:>9.4f}")
    print("=" * (W + 46))
    if total_in == 0 and total_out == 0:
        print("\n  ! All counts are 0 — the LLM proxy likely does not return")
        print("    token usage in its response. Check that your API returns")
        print("    a standard OpenAI-compatible 'usage' field.")


if __name__ == "__main__":
    asyncio.run(main())
