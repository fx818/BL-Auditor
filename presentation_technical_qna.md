# BuyLead Auditor Technical Q&A

## 1. Model Selection And Benchmarking

### 1. Why is DeepSeek V4 Flash the preferred replacement model instead of evaluating multiple candidates in parallel?

DeepSeek V4 Flash is being considered because Gemini 2.5 Flash Lite is expected to be discontinued, and DeepSeek offers stronger instruction-following benchmark numbers with competitive input/output cost. The right technical approach is still to benchmark DeepSeek against at least one fallback model before final production migration.

### 2. Are we comparing models on generic benchmarks, or only on our actual BuyLead Auditor tasks?

Generic benchmarks are useful for initial shortlisting, but the final decision should be based on our actual BuyLead Auditor workload. The model must be tested on Inter ISQ, Description, Buyer Viewed Products, Retail Agent, and final BL quality scoring behavior.

### 3. How will we measure whether DeepSeek is competitive with Gemini: agent-level metrics, final BL quality score, or both?

Both are required. Agent-level metrics show where the model improves or regresses, while final BL quality score accuracy shows whether the system-level business decision remains stable.

### 4. Since Gemini has higher TPS and lower latency, what technical tradeoff are we accepting by moving to DeepSeek?

DeepSeek is slower on the provided numbers: 76 tok/s and 0.65s latency versus Gemini's 108 tok/s and 0.36s latency. The tradeoff is accepting some latency risk in exchange for continuity after Gemini discontinuation and potentially better instruction following.

### 5. DeepSeek has stronger instruction-following benchmark numbers. How do we validate that this translates to better structured JSON and better BL decisions in our prompts?

We validate it empirically by measuring JSON parse success, schema compliance, label correctness, and agent-wise accuracy on the same benchmark set. Better instruction-following benchmark numbers are not enough unless they improve our actual prompts and outputs.

## 2. Prompt And Output Contract Reliability

### 6. Are the current prompts tuned specifically for Gemini behavior, and could that create regression when moved to DeepSeek?

Yes, that is possible. Even model-agnostic prompts often become tuned to the behavior of the model used during development. DeepSeek may interpret instructions, edge cases, and confidence labels differently, so prompt compatibility must be tested.

### 7. Will we first run DeepSeek with unchanged prompts to get a fair baseline before doing prompt tuning?

That should be the plan. First run the same prompts to compare model behavior fairly against Gemini. If DeepSeek underperforms, then tune prompts and re-evaluate against the same validation criteria.

### 8. What is the fallback if DeepSeek returns malformed JSON or extra prose despite strict output instructions?

The code already attempts JSON extraction from model output, but production should also track parse failures per agent. If parse failures increase, we need stricter prompts, response-format support where available, retry logic, and graceful fallback to an unavailable/error status.

### 9. Do all agents use the same response schema style, or do we have schema differences that increase integration risk?

There are schema differences. Inter ISQ and Description return status/score/confidence/reason/issues style outputs. Retail Agent returns classification/confidence/reason. Buyer Viewed Products returns genuineness/profile score/product match. These differences increase the importance of per-agent schema validation.

### 10. How do we detect silent schema drift where the model returns valid JSON but with semantically wrong labels or fields?

Use strict allowed-value validation, field-level checks, and monitoring for unusual label distributions. Valid JSON is not enough; the output must also use expected values like Correct/Incorrect/Not Available, Retail/Non-Retail, and confidence levels consistently.

## 3. Agent-Level Technical Failure Modes

### 11. Which local agent is most sensitive to model reasoning quality: Inter ISQ, Description, Buyer Viewed Products, or Retail?

Retail Agent is likely the most reasoning-sensitive because it infers intent from product, quantity, unit, and real-world purchase behavior. Description and Buyer Viewed also require semantic reasoning, while Inter ISQ has some contradictions that can be partly rule-based.

### 12. Retail Agent is weaker than the others. Is the weakness caused by prompt ambiguity, insufficient input features, or model limitation?

It may be all three. Retail/non-retail intent is inherently ambiguous, current inputs may not capture enough buyer context, and the model may struggle with borderline quantities or mixed-use products. This agent needs separate analysis rather than being hidden inside aggregate metrics.

### 13. For Buyer Viewed Products, how do we prevent the model from overusing weak historical signals when product history is sparse?

The prompt should treat no history or very sparse history as low-confidence, not as negative evidence. Product history should be a supporting signal, and the final score should avoid over-penalizing genuine buyers exploring new categories.

### 14. For Description Agent, how do we control the balance between being forgiving and missing truly unrelated descriptions?

The agent is intentionally forgiving to avoid false rejection of valid but short or broad descriptions. To control risk, we should review false positives, monitor low-confidence Correct outputs, and define stricter handling for clearly unrelated or garbage descriptions.

### 15. For Inter ISQ, which contradictions are deterministic enough to move from LLM logic into rule-based checks?

Quantity equal to mobile number or PIN code is already handled as a deterministic hard check. Other candidates include duplicate total quantity fields with conflicting values, negative quantities, obvious garbage values, and exact unit-convertible contradictions.

## 4. Evaluation Design

### 16. Is the 400-BL evaluation set large enough to detect small but business-relevant regressions after model migration?

It is enough for an initial baseline, but not enough by itself for full migration confidence. Small regressions can be missed in 400 samples, especially within low-frequency categories or edge cases.

### 17. Do we have a separate hard-case set for ambiguous descriptions, mixed buyer history, and retail/non-retail borderline quantities?

That should be created if not already available. A representative random set measures average production performance, while a hard-case set exposes failures that aggregate accuracy can hide.

### 18. Will we compare confusion matrices, not just accuracy/precision/recall, before approving the model switch?

Yes, that is important. Confusion matrices show exactly how many Correct cases become Incorrect and how many Incorrect cases pass as Correct. That is necessary for understanding business risk.

### 19. Do we evaluate category-wise and language-wise performance, or only aggregate performance?

We should evaluate both. Aggregate performance can look strong while specific categories, Hindi/Hinglish descriptions, sparse ISQs, or retail-heavy categories degrade.

### 20. How do we prevent prompt tuning on DeepSeek from overfitting to the 400-BL benchmark?

Keep a holdout set that is not used during prompt tuning, add fresh production BLs, and report performance separately on benchmark, hard-case, and holdout sets. Prompt changes should be accepted only if they improve general performance, not just the known examples.

## 5. Production Performance And Reliability

### 21. Since agents run concurrently, what is the expected end-to-end latency after switching to a slower per-token model?

Because agents run concurrently, total audit latency is driven mostly by the slowest parallel call, not the sum of all calls. Still, DeepSeek's higher latency and lower TPS can increase end-to-end time, so this must be measured in a full pipeline test.

### 22. What timeout, retry, and fallback behavior exists if one LLM agent fails while the rest succeed?

The agents include retry behavior, and the audit route captures per-agent errors instead of necessarily failing the entire audit. Failed agents can return fallback/error-style outputs while other agent results continue to be used.

### 23. Can we degrade gracefully by marking one agent as unavailable without failing the entire BL audit?

Yes, that is the preferred behavior. For production reliability, one failed LLM agent should not block the full audit unless that agent is mandatory for the business decision.

### 24. What production metrics will we monitor: latency, parse failures, agent errors, disagreement rate, score drift, and cost per BL?

All of these should be monitored. The most important technical metrics are per-agent latency, timeout rate, retry rate, parse failure rate, fallback rate, schema violation rate, final score distribution drift, and cost per BL.

### 25. What is the rollback plan if DeepSeek passes offline testing but degrades after production rollout?

Use a phased rollout with shadow testing and canary traffic first. Define rollback thresholds before launch, such as increased parse failures, latency breach, agent-wise accuracy degradation, or final BL score drift. Keep the previous model/provider available until DeepSeek is stable.
