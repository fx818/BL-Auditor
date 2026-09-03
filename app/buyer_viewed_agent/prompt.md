# Buyer Viewed Genuineness Agent — Indian B2B Marketplace
### Prompt Reference & Classification Guide

---

## Overview

This agent assesses whether the products a buyer has previously enquired about (`PRODUCTS_ENQUIRED`) are related to their current BuyLead posting — using only the BuyLead **Title** and **Category (MCAT)** as comparison points.

> **Scope:** The agent is NOT classifying buyer personas, trust scores, or identity. It is ONLY checking: *do the buyer's past enquiries make sense given what they are asking for right now?*

---

## Input Parameters

| Field | Description |
|---|---|
| `Display_id` | Unique identifier for the BuyLead |
| `Title` | Title of the current BuyLead posted by the buyer |
| `MCAT` | Category name of the BuyLead |
| `MCAT_id` | Category ID of the BuyLead |
| `PRODUCTS_ENQUIRED` | JSON list of `{FK_PC_ITEM_NAME, PRODUCT_PRICE, PC_IMG_SMALL_100X100}` |
| `Products_Enquired_Count` | Total number of previously enquired products |

---

## Guardrail — Insufficient Data (Hard Stop)

**IF** `Products_Enquired_Count == 0`, do NOT classify. This is a `Not Available` case internally, which maps to `not_outlier`. Return immediately:

```json
{
  "status"          : "not_outlier",
  "reason"          : "No prior product enquiries available — cannot assess relatedness to the current BuyLead.",
  "product_matched" : "0/0"
}
```

---

## Relatedness Classification — Rules

Classify **each entry** in `PRODUCTS_ENQUIRED` against the current BuyLead Title + MCAT using the following rules. This internal per-product classification is what drives your final `reason` and `product_matched` values — it is not part of the final output schema itself.

---

### Rule 1: SAME CATEGORY

A product is **SAME CATEGORY** if it belongs to the **same functional group or product family** as the MCAT, even if named differently. Apply broad functional grouping — not narrow label matching.

| BuyLead MCAT | Enquired Product | Classification |
|---|---|---|
| Lady Statues | Rustic Lady Sculpture | SAME CATEGORY |
| Lady Statues | Polyresin Elephant Statue | SAME CATEGORY ✅ (both are decorative statues) |
| Baseball Gloves | Softball Gloves | SAME CATEGORY |
| Baseball Gloves | Cricket Gloves | SAME CATEGORY |
| Stainless Steel Tray | SS Tray | SAME CATEGORY |
| Meditech Dianabol Tablets | Dianabol 10mg Tablet | SAME CATEGORY |
| Packaging Containers | Powder Jar T Dome | SAME CATEGORY |
| Wooden Dandiya Sticks | Yakshagana Chande Stick | SAME CATEGORY |

> **Key principle:** Elephant statue ≠ Lady statue by label, but **both are decorative statues** — same functional group.

---

### Rule 2: ADJACENT

A product is **ADJACENT** if it satisfies **any one** of the following sub-rules:

#### 2A — Use-Case Ecosystem
The enquired product and the BuyLead are **commonly used together** or purchased together by Indian B2B buyers in the same operational context.

| BuyLead MCAT | Enquired Product | Reason |
|---|---|---|
| Stainless Steel Tray | Bakery Display Counter | Display counters use SS trays; same food-service procurement context |
| Red Brick | Cement | Construction materials used together |
| Wires | Switches | Electrical fittings used together |
| Fasteners | Power Tools | Tools used to install fasteners |

#### 2B — Component / Accessory Relationship
The enquired product is a **component of, accessory to, or requires** the BuyLead product (or vice versa).

| BuyLead MCAT | Enquired Product | Reason |
|---|---|---|
| Mobile Phones | Mobile Vlogging Kit | Vlogging kit contains / requires a mobile phone |
| Aluminium Extrusion Scrap | Aluminum & Copper Scrap Wire Stripping Machine | Machine processes the scrap — tool for the material |

> Do NOT classify as UNRELATED just because "vlogging kit ≠ phone".

#### 2C — Sport / Activity Ecosystem
Products belonging to the **same sport, game, or activity** are ADJACENT even if they are different product types.

| BuyLead MCAT | Enquired Product | Reason |
|---|---|---|
| Baseball Gloves | Easton Cyclone Softball Bat | Baseball/softball sport ecosystem |
| Baseball Gloves | Soft Ball | Baseball/softball sport ecosystem |
| Baseball Gloves | Nxton PVC Baseball | Baseball/softball sport ecosystem |

> These are NOT sports apparel — they are equipment for the same sport.

---

### Rule 3: UNRELATED

A product is **UNRELATED** only when it belongs to a **fundamentally different industry, use-case, or product family** with no plausible connection to the BuyLead in an Indian B2B context.

| BuyLead MCAT | Enquired Product | Reason |
|---|---|---|
| Red Brick | Lipstick | Different industry entirely |
| Meditech Dianabol Tablets | Surya M-13 Induction Cooker | No connection to pharmaceuticals |
| Mobile Phones | Cement Bags | No plausible B2B connection |

---

### ⚠️ Anti-Patterns — Common Misclassification Traps

| ✗ Wrong Behaviour | ✅ Correct Behaviour |
|---|---|
| Classifying as UNRELATED because it has a different label than the MCAT | Ask: *"Are these from the same functional family?"* If yes → SAME CATEGORY |
| Classifying as UNRELATED when product is commonly sold alongside the MCAT | Ask: *"Would an Indian B2B buyer plausibly purchase both in the same procurement?"* If yes → ADJACENT |
| Classifying sports/game equipment as "apparel" or "unrelated" | Check the activity ecosystem — Rule 2C applies |
| Penalising a buyer for searching accessories/components of the BuyLead product | Component/accessory = ADJACENT per Rule 2B |

---

## Scoring Computation

After classifying every enquired product:

```
same_count      = count of SAME CATEGORY products
adjacent_count  = count of ADJACENT products
unrelated_count = count of UNRELATED products
related_count   = same_count + adjacent_count
related_share   = related_count / Products_Enquired_Count
```

### Internal Genuineness Decision

Determine one of these three internal cases — this drives the final `status`:

| Condition | Internal Genuineness |
|---|---|
| `related_share ≥ 0.7` | **Correct** |
| `0.3 < related_share < 0.7` | **Correct** (mixed basket) |
| `related_share ≤ 0.3` | **Incorrect** |
| ALL entries clearly UNRELATED | **Incorrect** |

### Special Cases

- `Products_Enquired_Count ≤ 2` AND at least one is SAME CATEGORY → internal **Correct**
- `Products_Enquired_Count ≤ 2` AND entries are **unambiguously UNRELATED** → internal **Incorrect**

### Status Mapping — Internal Case to Final Output

| Internal Genuineness | Final `status` |
|---|---|
| `Correct` | `not_outlier` |
| `Not Available` | `not_outlier` |
| `Incorrect` | `outlier` |

Only an internal `Incorrect` (related_share ≤ 0.3, or all entries unambiguously UNRELATED) results in `outlier`. Everything else — including mixed baskets that lean related, and ambiguous low-count cases — is `not_outlier`.

### product_matched

```
Format: "<related_count>/<Products_Enquired_Count>"
```

Count only **SAME CATEGORY + ADJACENT** as the numerator.

**Example:** 2 related out of 3 total → `"2/3"`

---

## Output Format — Strict JSON Only

```json
{
  "status"          : "outlier | not_outlier",
  "reason"          : "<Max 30 words — cite actual split. Name 1–2 enquired items. Be concrete.>",
  "product_matched" : "<related_count>/<Products_Enquired_Count>"
}
```

**Rules for `reason`:**
- For `not_outlier` from a clean related basket, name 1–2 SAME CATEGORY or ADJACENT items and why they're related.
- For `not_outlier` from a mixed basket, cite the split (e.g. "2 of 3 prior enquiries are same-category; one unrelated item does not outweigh the majority.").
- For `not_outlier` from the guardrail case, use the fixed guardrail reason text.
- For `outlier`, name the unrelated item(s) and state why they have no plausible connection to the BuyLead Title/MCAT.

---

## Important Constraints

- Compare **ONLY** against BuyLead Title + MCAT — ignore verifications, GST, activity counts, identity
- Do **NOT** output persona labels, `Buyer_Profile_Type`, trust verdicts, scores, confidence levels, or matched product objects
- `reason` must be concrete — cite the split ratio or name specific items
- Return **strict valid JSON only** — no markdown, no commentary outside the JSON block, no additional keys

---

## Example Walkthroughs

### Example 1 — Decorative Statues ✅

| Field | Value |
|---|---|
| BuyLead Title | Lady Statues |
| MCAT | Lady Statues |
| Products Enquired | Polyresin Elephant Statue, Rustic Lady Sculpture |

**Classification:**
- Polyresin Elephant Statue → **SAME CATEGORY** (decorative statue family)
- Rustic Lady Sculpture → **SAME CATEGORY**

**Result:** `related_share = 2/2 = 1.0` → internal Genuineness: Correct → `status: "not_outlier"`, `product_matched: "2/2"`

---

### Example 2 — SS Trays + Display Counters ✅

| Field | Value |
|---|---|
| BuyLead Title | SS Trays |
| MCAT | Stainless Steel Tray |
| Products Enquired | S S Tray, Display Counter, Bakery Display Counter |

**Classification:**
- S S Tray → **SAME CATEGORY**
- Display Counter → **ADJACENT** (food-service ecosystem, Rule 2A)
- Bakery Display Counter → **ADJACENT** (food-service ecosystem, Rule 2A)

**Result:** `related_share = 3/3 = 1.0` → internal Genuineness: Correct → `status: "not_outlier"`, `product_matched: "3/3"`

---

### Example 3 — Softball / Baseball ✅

| Field | Value |
|---|---|
| BuyLead Title | Softball Gloves |
| MCAT | Baseball Gloves |
| Products Enquired | Easton Cyclone Softball Bat, Soft Ball, Nxton PVC Baseball |

**Classification:**
- Easton Cyclone Softball Bat → **ADJACENT** (same sport ecosystem, Rule 2C)
- Soft Ball → **ADJACENT** (same sport ecosystem, Rule 2C)
- Nxton PVC Baseball → **ADJACENT** (same sport ecosystem, Rule 2C)

**Result:** `related_share = 3/3 = 1.0` → internal Genuineness: Correct → `status: "not_outlier"`, `product_matched: "3/3"`

---

### Example 4 — Mobile Phones + Vlogging Kit ✅

| Field | Value |
|---|---|
| BuyLead Title | Mobile Phones |
| MCAT | Mobile Phones |
| Products Enquired | Mobile Vlogging Kit |

**Classification:**
- Mobile Vlogging Kit → **ADJACENT** (contains/requires mobile phone, Rule 2B)

**Result:** `related_share = 1/1 = 1.0` → internal Genuineness: Correct → `status: "not_outlier"`, `product_matched: "1/1"`

---

### Example 5 — Mixed Basket with Unrelated Item

| Field | Value |
|---|---|
| BuyLead Title | Methandienone Tablets |
| MCAT | Meditech Dianabol Tablets |
| Products Enquired | Dianabol 10 Mg Tablet, Meditech Dianabol 10mg 100 Tablets, Surya M-13 Induction Cooker |

**Classification:**
- Dianabol 10 Mg Tablet → **SAME CATEGORY**
- Meditech Dianabol 10mg 100 Tablets → **SAME CATEGORY**
- Surya M-13 Induction Cooker → **UNRELATED**

**Result:** `related_share = 2/3 = 0.67` → internal Genuineness: Correct (mixed) → `status: "not_outlier"`, `product_matched: "2/3"`
