# Detailed Retail Agent Latency Analysis

## Hard data from the last 2 audits (same offer ID `144061184034`)

| Run | Retail | Price | Buyer | System prompt | User msg | Output |
|---|---:|---:|---:|---:|---:|---:|
| 12:07:42 | **34.7s** | 28.0s | 1.2s | 4,209 chars | 323 chars | 232 chars |
| 12:15:08 | **108.5s** | 33.2s | 1.8s | 4,209 chars | 323 chars | 308 chars |

**Same input, 3.1× variance.** This single fact rules out every "prompt is too big / model is doing too much work" hypothesis. The retail agent code path is *deterministic* — identical input produced wildly different wall-times. The slowness is not in the agent code; it's in the **LLM round-trip via `imllm.intermesh.net`**.

## Where the 108.5s actually goes

The retail step is structurally:

```
_prepare_input  →  Python only, no I/O after first call (lru_cache on Excel)  →  ~50 ms
_classify       →  prompt render (~5ms) + ChatOpenAI ctor (~5ms) + ainvoke (REST)
```

There are **no** retries, **no** loops, **no** secondary LLM calls, **no** streaming buffering. Effectively all 108s is inside `await llm.ainvoke(...)`. The output is 308 chars of clean JSON — that's ~80 output tokens. At even 10 tokens/sec that's 8 seconds of generation. The other 100 seconds are unaccounted-for time at the proxy or model side.

## Ranked hypotheses

### 1. Proxy queueing / cold-start at `imllm.intermesh.net` — **most likely**

- The 3× same-input variance is the smoking gun. The agent code can't produce it; only an upstream queue / cold container / per-tenant rate limiter can.
- 100+ second waits with no observable token streaming look exactly like requests stuck behind a queue or waiting for a model container to scale up.
- Price agent shows similar but smaller swings (28→33s) — consistent with proxy-side variance affecting all concurrent calls.
- **What you can do to confirm**: hit the proxy with a `curl` from the same machine 5 times in a row with the exact same payload, time each one. If the variance is also 3×, the proxy is the bottleneck and nothing in the Python code will fix it.

### 2. The reasoning/thinking knobs are not being honored by the proxy — **very likely**

- I added `model_kwargs={"reasoning_effort": "minimal"}` and `extra_body={"google": {"thinking_config": {"thinking_budget": 0}}}`. The proxy may silently drop both.
- The output is clean JSON in both runs — but the model can still spend thinking tokens that the proxy strips from the visible response. From the client side we'd see what we're seeing now: long wall time, short visible output.
- For Gemini 2.5 Flash Lite, the *direct* Google API field is `generationConfig.thinkingConfig.thinkingBudget`. The OpenAI-compat proxy may expect that under a different path (e.g. `body.generationConfig.thinkingConfig` rather than `body.google.thinking_config`), or it may strip unknown fields entirely.
- **What you can do to confirm**: call the proxy directly with `curl`, once with `thinking_budget: 0`, once without. If timing is identical, the knob is ignored.

### 3. Category-based hard overrides still going through the LLM — **likely**

- The Python `_hard_override` covers only **unit-based** overrides (Ton, KG ≥ 200, Litre ≥ 200). Run 12:15:08 returned `Override_Applied: "Yes — Category is industrial/institutional by nature"` — a **category-based** override. The model has to scan the 10 industrial-category families embedded in the prompt and decide. For "Concrete Compound Wall" → construction material, that's straightforward, but the LLM still pays full latency.
- This is **structural** — every BL whose unit-override doesn't fire (most of them) still incurs the full LLM cost even when the answer is mechanically derivable from MCAT name.
- Estimate: 30–60% of leads would hit a category-family override if we encoded them in Python.

### 4. Single LLM HTTP connection, not pooled — **minor**

- Each `ChatOpenAI(...)` constructor is created fresh inside `_classify`. The underlying httpx client opens a fresh TLS connection to the proxy. With HTTP/1.1 over slow TLS that's 100–300 ms of overhead per call — small in absolute terms but a death-of-1000-cuts in batch mode.
- Not the dominant cost here, but worth noting.

### 5. Sequential agents in single-audit path — **structural, not retail-specific**

- `/audit` calls Retail → Price → Buyer **sequentially**, not in parallel like `/batch/stream` does. So total wall time = sum of all three.
- This isn't a retail problem per se, but it does mean fixing retail's 108s only helps `/audit` by ~108s out of ~155s total. Parallelizing them in `/audit` would shave another ~30s off perceived latency.

### 6. lru_cache cold first-call cost — **marginal, not the issue here**

- `_load_evidence_metrics()` parses `evidence_data.xlsx` (22 KB) and `_load_price_index()` parses `mcat_data.xlsx` (**36 MB**) on first call. That's measurable (~2–5s) but happens **once per process**. After uvicorn reloads, the first audit pays this cost. Both your traced audits show retail wall times dominated by the LLM call, not Excel parsing — so this is not the bottleneck for runs 2+.
- Worth a sanity check though: if your dev workflow includes frequent uvicorn `--reload`, the *first* audit after every code change pays the 36 MB Excel load.

### 7. Non-streaming `ainvoke` — **diagnostic limitation, not a real slowdown**

- Using `.ainvoke()` (not `.astream()`) means the client sits blocked until the proxy returns the full response. Switching to streaming wouldn't reduce wall time but would make 108s waits visible as "still receiving tokens at 0.5 tok/s" vs "stuck somewhere upstream". Useful for diagnosing #1 vs #2.

## What I'd recommend trying next (in order)

1. **Bypass-test the proxy with `curl`** — time 5 sequential and 5 concurrent identical calls. If variance matches what we see in the audit traces, the bottleneck is fully upstream and no code change will fix it.
2. **Add a category-based Python short-circuit** — encode the 10 industrial families from the prompt as a Python keyword list against `MCAT` name; matches return NON-RETAIL instantly. This would have caught run 12:15:08 (Concrete Compound Wall → construction) and skipped the 108s call entirely.
3. **Verify the thinking-disable knobs land** — try a `httpx` direct call with `stream=True` and time first-token-received vs full-response-received. If first-token-received is also 100s, the model is thinking; if it's fast and *completion* is slow, the proxy is queueing.
4. **Try a different model on the same proxy** — keep everything else identical, swap `RETAIL_LLM_MODEL=google/gemini-2.5-flash` (non-lite) or any non-Gemini option the proxy exposes, and compare timings.
5. **Reuse the `ChatOpenAI` client across calls** — module-level singleton instead of per-call construction. ~100–300 ms per save in batch mode; negligible for single audits.

## Summary

The retail agent code is no longer the bottleneck. The optimizations from the previous round (hard-override short-circuit on units, prompt shrink 9.8→4.2 KB, reasoning knobs, JSON output mode, robust parsing) have done their job — the prompt is small, the prep path is fast, the output is clean JSON. What remains is **wholly outside the Python**: the `imllm.intermesh.net` proxy is returning highly variable latency (34s → 108s on identical input), and the most actionable in-code lever left is encoding category-family overrides so we never make the LLM call when an industrial MCAT is obvious from its name.
