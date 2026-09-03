# BuyLead Auditor Presentation Q&A

## 1. Business Objective Of BuyLead Auditor

### 1. What exact business problem is BuyLead Auditor solving: bad BL rejection, BL quality scoring, seller trust, operational review reduction, or all of these?

It is solving BL quality at multiple layers. The auditor checks whether buyer-provided information is coherent, whether the buyer behavior looks relevant, whether the requirement appears retail or non-retail, and then feeds those signals into a combined BL quality score. The objective is not only automation, but improving the reliability of BLs before they are trusted downstream.

### 2. How do we define a high-quality BuyLead in business terms, not just model terms?

A high-quality BL should represent a genuine, coherent buyer requirement that sellers can act on. In business terms, it should have internally consistent details, a meaningful description, relevant buyer behavior where available, and a classification that helps decide whether the BL is useful for the intended seller workflow.

### 3. If this auditor did not exist, what specific quality problems would reach sellers or internal teams?

Inconsistent ISQs, vague or unrelated descriptions, weak buyer intent signals, and questionable retail/non-retail classification would pass with less structured validation. That can increase manual review, reduce seller trust, and make the BL quality score less explainable.

### 4. Which stakeholder benefits most from the auditor: buyer, seller, operations team, product team, or revenue team?

The direct beneficiary is the seller because better BL quality improves trust and usability. Operations benefits through more structured review signals, product benefits through measurable quality controls, and revenue benefits indirectly because seller confidence in BLs affects engagement.

### 5. How does the auditor improve seller trust in BuyLeads?

It reduces the chance that incoherent, irrelevant, or low-intent BLs are treated the same as strong BLs. It also gives explainable reasons, so quality decisions are not black-box flags but tied to specific fields like ISQ, description, buyer activity, or retail intent.

### 6. Are we using the auditor only as a diagnostic layer, or does it directly influence action on a BL?

Currently the agents feed a combined BL quality score. The recommended position is that it should be treated as a decision-support and scoring layer, with stronger automation only where confidence and historical validation are high.

## 2. What The Auditor Checks

### 7. What does the Inter ISQ agent catch that other checks cannot catch?

It checks coherence inside the ISQ/spec rows themselves. For example, it can detect contradictions, duplicate total quantity fields, garbage values, or cases where quantity is actually a phone number or PIN code. Title/category checks cannot catch those internal conflicts.

### 8. What kind of Description issues are harmful enough to affect BL quality?

Descriptions that are unrelated to the title or category, structurally meaningless, contact-only with no product intent, or misleading enough to represent a different product family. Thin descriptions are not automatically bad; the risk is when the description weakens confidence in the buyer requirement.

### 9. Why is Buyer Viewed Products a valid signal for BL genuineness?

Past viewed or enquired products can show whether the buyer's current requirement fits their recent buying context. It is not treated as identity proof, but as a relatedness signal: same category, adjacent category, or unrelated basket.

### 10. Can a buyer with no prior viewed/enquired products still be a good buyer?

Yes. No prior data should not be treated as negative evidence. It should be `Not Available` or low-information, not `Incorrect`. A new or low-history buyer can still be genuine.

### 11. What does Retail Agent add to the BL quality score that quantity or category alone cannot provide?

It infers likely buyer intent from product nature, quantity, unit, and real-world usage. Quantity alone can mislead because the same quantity may be retail for one product and commercial for another.

### 12. Are these agents independent checks, or do they overlap in what they detect?

They are mostly complementary but not perfectly independent. Inter ISQ checks internal spec coherence, Description checks text coherence, Buyer Viewed checks behavior relevance, and Retail checks buyer intent. Some overlap is acceptable because BL quality is multi-signal.

### 13. Which agent is most critical to final BL quality, and why?

It depends on the failure type. Inter ISQ is critical for data consistency, Description for text relevance, Buyer Viewed for behavioral relevance, and Retail for intent classification. For final quality, the combined score matters more than any single agent.

## 3. BL Quality Impact

### 14. How do we know the auditor is improving real BL quality and not only matching labels in an evaluation set?

The current 400-BL benchmark shows model-label alignment, but real quality improvement must be validated through production monitoring: disagreement review, category-wise performance, manual audit samples, and downstream BL acceptance or complaint trends.

### 15. What percentage of bad BLs are we currently able to catch because of these LLM agents?

From the provided metrics, recall is high for most agents when the positive class is `Correct`, but to answer bad-BL catch rate precisely we need negative-class recall or confusion matrices. This should be called out honestly.

### 16. What kind of low-quality BLs still escape the auditor?

Borderline cases, subtle buyer-intent problems, new-category exploration, ambiguous descriptions, or commercially plausible but weak requirements can escape. The auditor is strong on coherence, but not a complete guarantee of business value.

### 17. Are we optimizing for catching maximum bad BLs, or avoiding wrongly penalizing good BLs?

The current agent design is conservative and generally leans toward `Correct` when ambiguous. That means it prioritizes avoiding wrongful penalties on genuine BLs, while still catching clear incoherence or mismatch.

### 18. If the auditor marks a BL as low quality, what downstream action is taken?

The prepared answer should be: it contributes to the combined BL quality score and can be used for review, routing, or prioritization. If a direct reject/suppress action is planned, it should be guarded by confidence thresholds and human review for borderline cases.

### 19. Does the auditor help more in obvious bad BL cases or in borderline ambiguous cases?

It helps in both, but its highest business value is in structured handling of ambiguous cases. Obvious bad BLs are easier to catch with rules; LLM agents add value where context and natural language interpretation matter.

### 20. Can the auditor explain its decision clearly enough for operations/product teams to trust it?

Yes, each agent returns a reason, confidence, and issue list or equivalent explanation. The important next step is making those reasons visible and reviewable in operations workflows.

### 21. What is the expected business impact if BL quality improves by even 1-2%?

At scale, even small quality improvements can reduce seller frustration, improve seller confidence in BLs, reduce review load, and improve downstream conversion quality. The exact financial impact should be tied to BL volume and seller engagement metrics.

## 4. Metrics And Evaluation Credibility

### 22. The benchmark is based on 400 BLs. How confident are we that this sample represents production traffic?

It is a useful baseline, not final proof. Confidence depends on whether the 400 BLs cover categories, languages, buyer types, edge cases, retail/non-retail mixes, and common low-quality patterns.

### 23. Were the 400 BLs selected randomly, or were they selected from known difficult/problematic cases?

This should be answered transparently. If mixed or curated, say so. The ideal next step is to evaluate on both a representative random sample and a hard-case sample.

### 24. Who labelled the 400 BLs, and how was label disagreement handled?

The strongest answer is: labels should be human-reviewed, with disagreement resolved through a second review or adjudication. If that process is not yet formalized, it should be presented as a required improvement before production-level confidence.

### 25. Since the positive class means Correct, how should leadership interpret precision and recall?

Precision means: when the agent says `Correct`, how often it is actually correct. Recall means: out of all actually correct BL cases, how many the agent successfully identifies as correct.

### 26. Are we more concerned about low precision or low recall for this use case?

Both matter, but the business tradeoff differs. Low precision means bad or questionable BLs may pass as correct. Low recall means genuine BLs may be flagged, causing manual review or lost opportunity.

### 27. Why do most agents show 97-98% accuracy, but Retail Agent is only 89.1%?

Retail intent is inherently more subjective. It depends on product type, quantity, use case, and real-world purchasing behavior. Unlike ISQ or description coherence, there is often no single obvious answer.

### 28. Are the metrics agent-wise only, or do we also have final combined BL quality score accuracy?

The listed metrics are agent-wise. For business readiness, final combined BL quality score accuracy should also be measured because downstream action depends on the combined outcome, not just individual agents.

### 29. Do we track performance by category, language, buyer type, and BL type?

That should be part of the monitoring plan. Aggregate accuracy can hide weak spots. Category-wise and segment-wise performance is needed before relying heavily on the score.

## 5. False Positive Risk

### 30. What happens when the auditor says a BL is Correct, but it is actually poor quality?

That is a false positive. The risk is that weak BLs enter downstream workflows and reduce seller trust. This is why precision, negative-case analysis, and periodic manual audits are important.

### 31. Which agent is most likely to wrongly pass a bad BL?

Retail Agent is the main concern because its current Gemini baseline is lower. Description can also be forgiving by design, especially where broad category relatedness is accepted.

### 32. What business damage can happen if bad BLs are passed as good?

Seller dissatisfaction, wasted seller time, reduced trust in BL quality, higher complaint or ignore rates, and weaker perceived value of the BL product.

### 33. Are false positives more dangerous than false negatives in this system?

For seller trust, false positives are often more dangerous because bad BLs reaching sellers directly damage confidence. For growth and availability, false negatives also matter because they can suppress genuine demand.

### 34. Do we have examples where the model was too lenient and marked a bad BL as correct?

This should be prepared with 2-3 real examples from evaluation. If not available, say that false-positive review is part of the next validation pass and should be tracked before rollout.

### 35. How do we prevent the auditor's lean-Correct behavior from allowing borderline bad BLs to pass?

Use confidence, combined score thresholds, negative-case sampling, and human review for borderline cases. The lean-Correct approach prevents over-rejection, but it must be balanced by monitoring false positives.

## 6. False Negative Risk

### 36. What happens when the auditor wrongly flags a genuine BL as problematic?

It can reduce valid BL availability, create unnecessary manual review, and potentially delay genuine buyer demand from reaching sellers.

### 37. Could this reduce valid BL availability for sellers?

Yes, if false negatives are used for direct suppression. That is why low-confidence or borderline flags should be routed to review rather than automatically treated as bad.

### 38. Which agent is most likely to wrongly penalize a genuine BL?

Retail Agent can misread ambiguous intent. Buyer Viewed can also be risky if a genuine buyer is exploring a new product category unrelated to prior behavior.

### 39. Do we have examples where genuine buyer intent was misunderstood by the auditor?

This should be prepared from evaluation disagreements. Common examples may include new-category buyers, broad descriptions, mixed product baskets, and retail/non-retail borderline quantities.

### 40. What is the escalation path when product/ops disagrees with the auditor?

The recommended path is human override plus feedback capture: record the disagreement, update labels, review prompt/threshold impact, and include such cases in the next benchmark set.

## 7. Agent-Specific Brutal Questions

### 41. Retail Agent has the weakest numbers: 89.1% accuracy, 85.7% precision, 83.3% recall. Why should it be trusted in the final score?

It should be trusted as one signal, not as a standalone final authority. Its output should be weighted carefully, monitored separately, and reviewed for borderline cases because retail intent is more subjective than coherence checks.

### 42. Is Retail classification inherently subjective, or is the current prompt/model not strong enough?

Both are possible. The task is inherently subjective because product, quantity, and use case interact. But prompt tuning, category-specific thresholds, and better examples can still improve it.

### 43. If Retail Agent is wrong, how much can it distort the final BL quality score?

That depends on scoring weight. This should be quantified through sensitivity analysis: compare final score distribution with and without Retail Agent, and measure how often Retail changes the final decision.

### 44. Buyer Viewed Products assumes past behavior predicts current genuineness. What if a genuine buyer is exploring a new category?

Then the agent should not over-penalize. No or unrelated history should be treated cautiously, especially with low sample count. Buyer Viewed is a supporting signal, not proof of genuineness.

### 45. Description Agent may mark broad related descriptions as correct. Could that make it too forgiving?

Yes, by design it avoids harsh rejection for broad but plausible descriptions. The mitigation is to use it with other agents and review false positives where unrelated descriptions were accepted too generously.

### 46. Inter ISQ checks internal consistency only. Could ISQs be internally consistent but still irrelevant to the actual BL?

Yes. Inter ISQ only checks whether the spec rows make sense together. Relevance to title/category is handled by other checks, so Inter ISQ should not be interpreted as full BL correctness.

### 47. Which agent gives the highest business value per LLM cost?

Likely Inter ISQ and Description, because they directly validate buyer-provided content and have strong baseline metrics. Buyer Viewed adds behavioral context. Retail needs more scrutiny because its metric gap may reduce value per call.

## 8. Operations, Adoption, And Monitoring

### 48. Who owns the final decision when auditor output conflicts with human judgment?

Product or operations should own the business decision, while the technical team owns model behavior, monitoring, and reliability. The auditor should provide evidence, not replace ownership without agreed thresholds.

### 49. After deployment, how will we monitor drift, wrong decisions, and category-wise degradation over time?

Track agent-wise metrics, final score distribution, category-wise disagreement, manual override rate, malformed output rate, latency, and sampled human review. Monitoring should compare current performance against the Gemini baseline and post-migration thresholds.

## 9. LLM Selection And Migration

### 50. Since Gemini 2.5 Flash Lite is expected to be discontinued, what is our fallback if DeepSeek does not match Gemini quality?

The fallback should be a parallel evaluation of at least one additional model/provider, plus phased rollout. DeepSeek should not be switched directly into production unless it is competitive with Gemini on BL quality.

### 51. Will DeepSeek be tested on the same 400 BLs plus fresh production BLs before switching?

Yes, that should be the plan. The same 400 BLs provide direct baseline comparison, while fresh production BLs reduce the risk of overfitting to a known test set.

### 52. Are prompts expected to work unchanged, or do we need model-specific prompt tuning before production use?

Initial testing should start with the same prompts for fair comparison. If DeepSeek underperforms or behaves differently, prompts may need tuning, but tuned results should be revalidated against the same quality gates.
