# BuyLead Auditor Presentation Questions

## 1. Business Objective Of BuyLead Auditor

1. What exact business problem is BuyLead Auditor solving: bad BL rejection, BL quality scoring, seller trust, operational review reduction, or all of these?

2. How do we define a high-quality BuyLead in business terms, not just model terms?

3. If this auditor did not exist, what specific quality problems would reach sellers or internal teams?

4. Which stakeholder benefits most from the auditor: buyer, seller, operations team, product team, or revenue team?

5. How does the auditor improve seller trust in BuyLeads?

6. Are we using the auditor only as a diagnostic layer, or does it directly influence action on a BL?

## 2. What The Auditor Checks

7. What does the Inter ISQ agent catch that other checks cannot catch?

8. What kind of Description issues are harmful enough to affect BL quality?

9. Why is Buyer Viewed Products a valid signal for BL genuineness?

10. Can a buyer with no prior viewed/enquired products still be a good buyer?

11. What does Retail Agent add to the BL quality score that quantity or category alone cannot provide?

12. Are these agents independent checks, or do they overlap in what they detect?

13. Which agent is most critical to final BL quality, and why?

## 3. BL Quality Impact

14. How do we know the auditor is improving real BL quality and not only matching labels in an evaluation set?

15. What percentage of bad BLs are we currently able to catch because of these LLM agents?

16. What kind of low-quality BLs still escape the auditor?

17. Are we optimizing for catching maximum bad BLs, or avoiding wrongly penalizing good BLs?

18. If the auditor marks a BL as low quality, what downstream action is taken?

19. Does the auditor help more in obvious bad BL cases or in borderline ambiguous cases?

20. Can the auditor explain its decision clearly enough for operations/product teams to trust it?

21. What is the expected business impact if BL quality improves by even 1-2%?

## 4. Metrics And Evaluation Credibility

22. The benchmark is based on 400 BLs. How confident are we that this sample represents production traffic?

23. Were the 400 BLs selected randomly, or were they selected from known difficult/problematic cases?

24. Who labelled the 400 BLs, and how was label disagreement handled?

25. Since the positive class means Correct, how should leadership interpret precision and recall?

26. Are we more concerned about low precision or low recall for this use case?

27. Why do most agents show 97-98% accuracy, but Retail Agent is only 89.1%?

28. Are the metrics agent-wise only, or do we also have final combined BL quality score accuracy?

29. Do we track performance by category, language, buyer type, and BL type?

## 5. False Positive Risk

30. What happens when the auditor says a BL is Correct, but it is actually poor quality?

31. Which agent is most likely to wrongly pass a bad BL?

32. What business damage can happen if bad BLs are passed as good?

33. Are false positives more dangerous than false negatives in this system?

34. Do we have examples where the model was too lenient and marked a bad BL as correct?

35. How do we prevent the auditor's lean-Correct behavior from allowing borderline bad BLs to pass?

## 6. False Negative Risk

36. What happens when the auditor wrongly flags a genuine BL as problematic?

37. Could this reduce valid BL availability for sellers?

38. Which agent is most likely to wrongly penalize a genuine BL?

39. Do we have examples where genuine buyer intent was misunderstood by the auditor?

40. What is the escalation path when product/ops disagrees with the auditor?

## 7. Agent-Specific Brutal Questions

41. Retail Agent has the weakest numbers: 89.1% accuracy, 85.7% precision, 83.3% recall. Why should it be trusted in the final score?

42. Is Retail classification inherently subjective, or is the current prompt/model not strong enough?

43. If Retail Agent is wrong, how much can it distort the final BL quality score?

44. Buyer Viewed Products assumes past behavior predicts current genuineness. What if a genuine buyer is exploring a new category?

45. Description Agent may mark broad related descriptions as correct. Could that make it too forgiving?

46. Inter ISQ checks internal consistency only. Could ISQs be internally consistent but still irrelevant to the actual BL?

47. Which agent gives the highest business value per LLM cost?

## 8. Operations, Adoption, And Monitoring

48. Who owns the final decision when auditor output conflicts with human judgment?

49. After deployment, how will we monitor drift, wrong decisions, and category-wise degradation over time?

## 9. LLM Selection And Migration

50. Since Gemini 2.5 Flash Lite is expected to be discontinued, what is our fallback if DeepSeek does not match Gemini quality?

51. Will DeepSeek be tested on the same 400 BLs plus fresh production BLs before switching?

52. Are prompts expected to work unchanged, or do we need model-specific prompt tuning before production use?
