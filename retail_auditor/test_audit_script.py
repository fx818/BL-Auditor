
from src.auditor_llm import AuditorLLM
import time

def test_logic():
    print("Initializing Auditor...")
    auditor = AuditorLLM() # Uses hardcoded creds
    
    print("\n--- Test Case 1: Bay Leaf 500kg (Industrial) ---")
    res1 = auditor.analyze_lead("Bay Leaf", 500, "kg", "High Value")
    print(f"Verdict: {res1.classification}")
    print(f"Reasoning: {res1.reasoning}")
    
    print("\n--- Test Case 2: Bay Leaf 1 Packet (Retail) ---")
    res2 = auditor.analyze_lead("Bay Leaf", 1, "Packet", "Low Value")
    print(f"Verdict: {res2.classification}")
    print(f"Reasoning: {res2.reasoning}")

if __name__ == "__main__":
    try:
        test_logic()
        print("\n✅ Verification Successful")
    except Exception as e:
        print(f"\n❌ Verification Failed: {e}")
