# Plan: Fix and Improve Slab Search & Evidence/Price Lookup

## Context

The retail agent and price agent both look up reference data by an (mcat_id, unit, slab) key. After inspecting `evidence_data.xlsx` (376 rows) and `mcat_data.xlsx` (311,420 rows) against the lookup code in `app/retail_agent/agent.py` and `app/price_agent/agent.py`, several real bugs and significant gaps were found. The current logic produces **silent wrong matches** for some BL inputs and produces **all-zero metrics** indistinguishable from "no data found" for others — which the LLM then misinterprets as a real signal.

---

## What is wrong today

### Bugs (correctness)

1. **Debug `print()` left in retail agent.** `app/retail_agent/agent.py:300-301` inside `_load_evidence_metrics()`:
   ```python
   print("key", key)
   print('metric by key', metrics_by_key)
   ```
   Floods stdout on every audit. Not present in `price_agent/agent.py`.

2. **`_get_slab` mishandles 0, negative, and `None` qty.** Current behaviour:
   - `_get_slab(0)` → `"1-10"` (semantically wrong — qty 0 is not in 1-10)
   - `_get_slab(-5)` → `"1-10"` (wrong)
   - `_get_slab(None)` → `""` (empty)

   Evidence loader separately treats `qty <= 0 OR no unit` as `("NO_UNIT", "no_slab")`. The BL side has no matching path — BL with qty=0 produces `"{mcat}-PIECE_1-10"` while the corresponding evidence row would be in `"{mcat}-NO_UNIT_no_slab"`. **Mismatch — silent wrong lookup.**

3. **BL-side and evidence-side fall-out logic differ.** Evidence loader collapses bad rows (qty≤0 or missing unit) into the `NO_UNIT_no_slab` bucket. BL side does not — it generates a normal-looking key from a corrupt qty.

### Sufficiency / search-technique gaps

4. **Heavy data sparsity, no fallback chain.** Of 21 distinct (mcat, unit) combos in evidence, only 7 cover all 6 slabs; 8 cover just 1 slab; 27 (mcat, unit, slab) buckets have only **1 row**. When the BL's exact (mcat, unit, slab) bucket is empty, the lookup silently returns all-zero metrics — the LLM cannot tell "0 approved BLs in our data" from "no data exists for this combo".

5. **No `evidence_count` exposed to the LLM.** `bl_apprvd=5` could come from 1 row or 50 rows; the prompt has no way to weight confidence by sample size.

6. **No `data_match_level` flag.** The agent_input gives no signal whether the metrics came from an exact slab match, a cross-slab fallback, or defaults.

7. **mcat_data.xlsx has duplicate column names** (`'median'` at positions 9 and 13, `'q3'` at positions 10 and 15). `dict(zip(headers, row_values))` keeps the *second* occurrence by accident. In the current dataset both columns happen to be identical for q3/median, so this is currently harmless — but it's a fragile coincidence that will silently break if the second column ever diverges (e.g. the user re-exports the sheet).

8. **`retail_ni` is computed and stored but never sent to the prompt.**

### Out-of-scope (acknowledged, not changing)

- mcat_data has only one row per (mcat, unit), so q1/median/q3 cannot be slab-tiered for the price agent. That's a data-structure choice, not a code bug.
- Helper-function duplication between the two agent files is real but is a separate refactor; not bundled here.

---

## Recommended changes

### A. `app/retail_agent/agent.py` and `app/price_agent/agent.py`

#### A1. Remove debug prints (retail only)
Delete `print("key", key)` and `print('metric by key', metrics_by_key)` at lines 300-301.

#### A2. Fix `_get_slab`
```python
def _get_slab(qty: float | None) -> str:
    if qty is None or qty <= 0:
        return "no_slab"
    if qty <= 10: return "1-10"
    if qty <= 25: return "11-25"
    if qty <= 50: return "26-50"
    if qty <= 100: return "51-100"
    if qty <= 200: return "101-200"
    return "200+"
```
Now `0`, negatives, and `None` collapse cleanly to `no_slab`, matching evidence-side semantics.

#### A3. Make BL-side fall-out match evidence-side
In `_prepare_input` (after `_extract_qty` + `_get_slab` + `_normalize_mcat_unit`), if the BL effectively has no usable qty/unit, override both unit and slab so the lookup hits the same bucket the evidence file used:
```python
if slab == "no_slab" or not norm_unit:
    norm_unit = f"{mcat_id}-NO_UNIT" if mcat_id else ""
    slab = "no_slab"
```

#### A4. Defensive mcat-column access
Replace `row.get("q1")`, `row.get("median")`, `row.get("q3")` in `_find_price_data` with positional access on the *first* statistical-group columns (col 8 = q1, col 9 = median, col 10 = q3). Stops relying on dict-overwrite semantics if the duplicate columns ever diverge.

#### A5. Track evidence-bucket sample size
In `_load_evidence_metrics`, add a `bucket_count` field (incremented per row contributing to that bucket). Surface it to the LLM as `evidence_count`.

#### A6. Add a unit-only cross-slab fallback (CONFIRMED)
In `_load_evidence_metrics`, build a *second* aggregation keyed by `{mcat}-{unit}` (no slab) summing across all slabs. Return both maps from the function.

In `_prepare_input`, after the exact-slab lookup misses, retry against the unit-only aggregate and tag the result:
- `evidence_match = "exact"` — exact (mcat, unit, slab) hit
- `evidence_match = "unit_only"` — cross-slab fallback used
- `evidence_match = "no_data"` — neither hit; metrics are zeros

Also tag the price lookup:
- `price_match = "exact"` if `_find_price_data` returned non-zero
- `price_match = "no_data"` if it fell through to defaults

#### A7. Pass new fields into the LLM user message and update prompts (CONFIRMED)
Add `evidence_match`, `evidence_count`, `price_match` to the user-message key list in both `_classify` (retail) and `_price_classify`. Update both `prompt.md` files to instruct the LLM: when `evidence_match == "no_data"` or `evidence_count` is small, lower confidence rather than treating zero metrics as a real signal.

#### A8. Wire `retail_ni` into the retail prompt (CONFIRMED)
Add `retail_ni` to the retail `_msg_keys` list in `_classify` and update `app/retail_agent/prompt.md` to reference it alongside the other approval/purchase metrics.

### B. Files to modify
- `app/retail_agent/agent.py` — A1–A7 + A8 (`retail_ni` wired into user message)
- `app/price_agent/agent.py` — A2–A7 (no debug print, no retail_ni)
- `app/retail_agent/prompt.md` — add `retail_ni`, `evidence_match`, `evidence_count`; low-data confidence guidance
- `app/price_agent/prompt.md` — add `evidence_match`, `evidence_count`, `price_match`; same guidance

No changes to router, CSV log, templates, or trace_service required (new fields flow through `agent_input` and are captured by the existing trace sub_steps).

---

## Verification

1. **Boundary tests (Python shell):**
   ```python
   from app.retail_agent.agent import _get_slab
   assert _get_slab(None) == "no_slab"
   assert _get_slab(0) == "no_slab"
   assert _get_slab(-5) == "no_slab"
   assert _get_slab(10) == "1-10"
   assert _get_slab(11) == "11-25"
   assert _get_slab(200) == "101-200"
   assert _get_slab(201) == "200+"
   ```

2. **End-to-end with real offer IDs:**
   - Offer with (mcat, unit, slab) IN evidence — confirm `evidence_match == "exact"` and counts > 0.
   - Offer where mcat/unit IS in evidence but slab is NOT — confirm `evidence_match == "unit_only"`.
   - Offer whose mcat/unit is NOT in evidence — confirm `evidence_match == "no_data"`, metrics zero.
   - Offer with qty=0 in ISQ — confirm BL side resolves to `no_slab` and matches evidence's NO_UNIT_no_slab bucket.

3. **Trace inspection:** `/traces/{trace_id}` shows new fields in Retail/Price Agent Function Trace.

4. **Regression check** on offer `142764424452` (demo sample) — pipeline still runs end-to-end.
