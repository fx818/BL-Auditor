# BuyLead Auditor Technical Questions

## 1. Model Selection And Benchmarking

1. Why is DeepSeek V4 Flash the preferred replacement model instead of evaluating multiple candidates in parallel?

2. Are we comparing models on generic benchmarks, or only on our actual BuyLead Auditor tasks?

3. How will we measure whether DeepSeek is competitive with Gemini: agent-level metrics, final BL quality score, or both?

4. Since Gemini has higher TPS and lower latency, what technical tradeoff are we accepting by moving to DeepSeek?

5. DeepSeek has stronger instruction-following benchmark numbers. How do we validate that this translates to better structured JSON and better BL decisions in our prompts?

## 2. Prompt And Output Contract Reliability

6. Are the current prompts tuned specifically for Gemini behavior, and could that create regression when moved to DeepSeek?

7. Will we first run DeepSeek with unchanged prompts to get a fair baseline before doing prompt tuning?

8. What is the fallback if DeepSeek returns malformed JSON or extra prose despite strict output instructions?

9. Do all agents use the same response schema style, or do we have schema differences that increase integration risk?

10. How do we detect silent schema drift where the model returns valid JSON but with semantically wrong labels or fields?

## 3. Agent-Level Technical Failure Modes

11. Which local agent is most sensitive to model reasoning quality: Inter ISQ, Description, Buyer Viewed Products, or Retail?

12. Retail Agent is weaker than the others. Is the weakness caused by prompt ambiguity, insufficient input features, or model limitation?

13. For Buyer Viewed Products, how do we prevent the model from overusing weak historical signals when product history is sparse?

14. For Description Agent, how do we control the balance between being forgiving and missing truly unrelated descriptions?

15. For Inter ISQ, which contradictions are deterministic enough to move from LLM logic into rule-based checks?

## 4. Evaluation Design

16. Is the 400-BL evaluation set large enough to detect small but business-relevant regressions after model migration?

17. Do we have a separate hard-case set for ambiguous descriptions, mixed buyer history, and retail/non-retail borderline quantities?

18. Will we compare confusion matrices, not just accuracy/precision/recall, before approving the model switch?

19. Do we evaluate category-wise and language-wise performance, or only aggregate performance?

20. How do we prevent prompt tuning on DeepSeek from overfitting to the 400-BL benchmark?

## 5. Production Performance And Reliability

21. Since agents run concurrently, what is the expected end-to-end latency after switching to a slower per-token model?

22. What timeout, retry, and fallback behavior exists if one LLM agent fails while the rest succeed?

23. Can we degrade gracefully by marking one agent as unavailable without failing the entire BL audit?

24. What production metrics will we monitor: latency, parse failures, agent errors, disagreement rate, score drift, and cost per BL?

25. What is the rollback plan if DeepSeek passes offline testing but degrades after production rollout?
