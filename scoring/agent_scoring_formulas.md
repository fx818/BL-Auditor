# Agent Scoring System — Mathematical Formulas

---

## 1. Notation

| Symbol | Definition |
|---|---|
| `A` | Set of all agents |
| `A_na` | Subset of agents returning Not Available |
| `A_avail` | `A` minus `A_na` (available agents only) |
| `w_i` | Base weight of agent i (given, sums to 100) |
| `w_i'` | Adjusted weight of agent i after redistribution |
| `s_i` | Agent score for agent i (0.0 to 1.0) |
| `c_i` | Confidence level of agent i (high / medium / low) |
| `S` | Final composite score (1% to 100%) |

---

## 2. Weight Redistribution

### 2a. Unavailable Weight

Sum the weights of all agents that returned Not Available:

```
W_na = SUM w_i   for all i in A_na
```

### 2b. Available Total Weight

Sum the weights of all remaining available agents:

```
W_avail = SUM w_i   for all i in A_avail
```

### 2c. Adjusted Weight Formula

Each available agent absorbs a share of the unavailable weight, proportional to its own base weight:

```
              w_i
w_i' = w_i + ------- × W_na      for all i in A_avail
             W_avail
```

**Key properties:**
- Heavier agents absorb more of the redistributed weight, preserving the relative importance ordering.
- Total adjusted weight always sums to 100: `SUM w_i' = 100` for all i in `A_avail`.
- If `W_na = 0` (no agents are unavailable), then `w_i' = w_i` for all agents.

---

## 3. Agent Score Function `s_i`

### 3a. Binary Agents

Binary agents have no confidence dimension. Score is either 0 or 1:

| Status | `s_i` |
|---|---|
| Correct | 1.0 |
| Not Correct | 0.0 |

> Applies to: Specs vs Category, Title vs Category, Title vs Specs.

### 3b. Confidence Agents — Correct or Retail

When status is Correct (or Retail / Non-retail for Retail Classification), the score scales with confidence:

| Confidence `c_i` | `s_i` |
|---|---|
| High | 1.0 |
| Medium | 0.6 |
| Low | 0.3 |

### 3c. Confidence Agents — Incorrect

When status is Incorrect, the score is penalised. A high-confidence incorrect result is penalised more severely than a low-confidence one:

| Confidence `c_i` | `s_i` | Rationale |
|---|---|---|
| High | 0.0 | Confident it is wrong — full penalty |
| Medium | 0.2 | Some doubt — partial penalty |
| Low | 0.4 | Low confidence in verdict — reduced penalty |

### 3d. Unified Score Function

```
         1.0                       if binary and status = Correct
         0.0                       if binary and status = Not Correct
         mult_correct[c_i]         if conf agent and status = Correct / Retail
s_i  =
         mult_incorrect[c_i]       if conf agent and status = Incorrect
         undefined                 if status = Not Available  (agent excluded)

Where:
  mult_correct   = { high: 1.0,  medium: 0.6,  low: 0.3 }
  mult_incorrect = { high: 0.0,  medium: 0.2,  low: 0.4 }
```

---

## 4. Composite Score

### 4a. Weighted Sum

The raw score is the weighted average of all available agent scores:

```
           SUM ( s_i × w_i' )    for all i in A_avail
S_raw  =  ─────────────────────────────────────────────
                SUM ( w_i' )     for all i in A_avail
```

### 4b. Final Score

Convert to a percentage, rounded to the nearest integer:

```
S = ROUND( S_raw × 100 )      S ∈ [1, 100]
```

---

## 5. Verdict Thresholds

| Score `S` | Verdict |
|---|---|
| S ≥ 75% | **Approved** |
| 30% ≤ S < 75% | **Needs Review** |
| S < 30% | **Do Not Approve** |

---

## 6. Worked Example

**Setup:** Description Coherence is Not Available. All other agents set.

| Agent | `w_i` | Status | Conf | `s_i` | `w_i'` | `s_i × w_i'` |
|---|---|---|---|---|---|---|
| Specs vs Category | 20 | Correct | — | 1.0 | 22.2 | 22.2 |
| Title vs Category | 20 | Correct | — | 1.0 | 22.2 | 22.2 |
| Title vs Specs | 15 | Not Correct | — | 0.0 | 16.7 | 0.0 |
| ISQ Validation | 15 | Correct | High | 1.0 | 16.7 | 16.7 |
| Buyer Profile | 10 | Incorrect | Medium | 0.2 | 11.1 | 2.2 |
| Retail Classification | 10 | Retail | High | 1.0 | 11.1 | 11.1 |
| Description Coherence | 10 | Not Available | — | — | 0 | excluded |
| **Totals** | **100** | | | | **100.0** | **74.4** |

**Step 1 — Redistribute weight:**

```
W_na    = 10
W_avail = 90
w_i'    = w_i × (100 / 90) = w_i × 1.111
```

**Step 2 — Compute raw score:**

```
S_raw = 74.4 / 100.0 = 0.744
```

**Step 3 — Final score:**

```
S = ROUND( 0.744 × 100 ) = 74%
```

**Verdict: Needs Review** (30% ≤ 74% < 75%)
