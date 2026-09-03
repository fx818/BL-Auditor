import json
import requests
from dataclasses import dataclass
from typing import Optional, Dict, Any

@dataclass
class AuditResult:
    classification: str  # "Retail" or "Non-Retail"
    confidence: str      # "High", "Medium", "Low"
    reasoning: str

DEFAULT_SYSTEM_PROMPT = '''

You are a Retail Sanity Auditor.

Your task is to determine the most likely buyer intent behind an order:
"Retail": Intended for end-use consumption, small-scale resale, or non-industrial use.
"Non-Retail": Intended for resale at scale, commercial operations, industrial processing, manufacturing, or institutional use.

Retail does NOT strictly mean household use.
Retail may include shops, cafes, small offices, or individual buyers purchasing reasonable quantities for non-industrial purposes.
Resale alone does NOT imply Non-Retail; scale and context matter.

Base your decision on:
The nature of the product and its typical buyers
Quantity and unit of measurement
Packaging or trade terminology (e.g., tonne, quintal, MT, drum, bulk lot)
Whether the product inherently implies industrial or commercial usage
Real-world common sense about buying intent

IMPORTANT INTENT GUIDELINES:

• Quantity is a signal, not a rigid rule — but it still matters.
• Consider whether the quantity falls within a "plausible end-use range" for a single buyer.

Some products are typically owned in very limited numbers per buyer
(e.g., vehicle attachments, large appliances, durable consumer goods).
For such products:
A quantity exceeding what a single end-user would reasonably own
  should strongly increase the likelihood of Non-Retail intent,
  even if the product is consumer-oriented.

For consumer products commonly purchased in small multiples
(e.g., heaters, fans, lights, furniture):
Low multiples may still indicate Retail intent
Higher multiples that suggest stocking, redistribution, or institutional use
  should bias toward Non-Retail classification

Trade units, bulk packaging, or unusually high multiples
are strong signals of commercial or resale intent.

Use probabilistic reasoning and contextual judgment.
Do NOT use rigid numeric thresholds.
If intent is ambiguous, choose the most reasonable classification and LOWER the confidence.

Before finalizing, sanity-check your decision by asking:
"Would a single end-user realistically own or need this many units?"
If not, adjust the classification or confidence accordingly.

Return ONLY valid RAW JSON with the following fields:
classification ("Retail" or "Non-Retail")
confidence ("High", "Medium", or "Low")
reasoning (brief explanation) '''


DEFAULT_FEW_SHOTS = """
Example:
{
  "order": {
    "product": "Cooking Oil",
    "quantity": 20,
    "unit": "Litre"
  },
  "answer": {
    "classification": "Non-Retail",
    "confidence": "Medium",
    "reasoning": "While cooking oil is consumer-oriented, this quantity exceeds typical end-use and is more consistent with procurement for commercial food operations or redistribution rather than individual or micro-scale usage."
  }
}

Example:
{
  "order": {
    "product": "Industrial Solvent",
    "quantity": 2,
    "unit": "Litre"
  },
  "answer": {
    "classification": "Non-Retail",
    "confidence": "High",
    "reasoning": "Primarily an industrial product regardless of quantity."
  }
}

Example:
{
  "order": {
    "product": "Wheat",
    "quantity": 1,
    "unit": "Quintal"
  },
  "answer": {
    "classification": "Non-Retail",
    "confidence": "High",
    "reasoning": "Use of trade units like quintal strongly indicates procurement for resale, storage, or commercial handling rather than end consumption."
  }
}

Example:
{
  "order": {
    "product": "Dry Fruits",
    "quantity": 2,
    "unit": "KG"
  },
  "answer": {
    "classification": "Retail",
    "confidence": "Low",
    "reasoning": "Could be personal use or small resale; intent is ambiguous."
  }
}

Example:
{
  "order": {
    "product": "Dry Fruits",
    "quantity": 10,
    "unit": "KG"
  },
  "answer": {
    "classification": "Non-Retail",
    "confidence": "Medium",
    "reasoning": "Quantity suggests resale-scale purchasing beyond typical end consumption."
  }
}

Example:
{
  "order": {
    "product": "Power Drill Machine",
    "quantity": 1,
    "unit": "Piece"
  },
  "answer": {
    "classification": "Retail",
    "confidence": "Medium",
    "reasoning": "Single-unit tool purchase indicates individual or small professional use."
  }
}

Example:
{
  "order": {
    "product": "Cleaning Chemical",
    "quantity": 1,
    "unit": "Drum"
  },
  "answer": {
    "classification": "Non-Retail",
    "confidence": "High",
    "reasoning": "Industrial packaging strongly indicates commercial usage."
  }
}
"""


DEFAULT_RESULT_FORMAT = """
{
  "classification": "Retail" or "Non-Retail",
  "confidence": "High" or "Medium" or "Low",
  "reasoning": "Brief explanation"
}
"""


DEFAULT_USER_TEMPLATE = """
Analyze the following order data:
{
  "product": "{product}",
  "quantity": {quantity},
  "unit": "{unit}",
  "price_info": "{price_range}"
}{median_context}

Determine the most likely buyer intent based on intent, scale, and real-world buying behavior.

Return ONLY valid standard JSON (RFC 8259). 
Do NOT wrap in markdown code blocks. Do NOT use single quotes for keys or string values. 

Format:
{result_format}
"""


class AuditorLLM:
    def __init__(self, api_key: str = None, model_name: str = "google/gemini-2.5-pro"):
        # Hardcoded configuration
        self.api_key = "sk-BtTuPNyeEmtliMm8Gkei1A"
        self.base_url = "https://imllm.intermesh.net"
        self.model_name = model_name
        
    def analyze_lead(
        self,
        product: str,
        quantity: float,
        unit: str,
        price_range: str = "N/A",
        custom_system_prompt: Optional[str] = None,
        custom_few_shots: Optional[str] = None,
        custom_user_template: Optional[str] = None,
        median_value_info: Optional[str] = None
    ) -> AuditResult:
        """
        Sends a lead to the Custom LLM endpoint for classification.
        """
        base_url = self.base_url.rstrip("/")
        url = f"{base_url}/v1/chat/completions"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        # -----------------------------
        # SYSTEM PROMPT (RULES / BEHAVIOR)
        # -----------------------------
        if custom_system_prompt:
            system_prompt = custom_system_prompt
        else:
            system_prompt = DEFAULT_SYSTEM_PROMPT
        
        # -----------------------------
        # ASSISTANT FEW-SHOT EXAMPLES
        # -----------------------------
        if custom_few_shots:
            assistant_few_shots = custom_few_shots
        else:
            assistant_few_shots = DEFAULT_FEW_SHOTS

        # -----------------------------
        # USER PROMPT (CURRENT LEAD)
        # -----------------------------
        
        # Determine format and context
        current_result_format = DEFAULT_RESULT_FORMAT
        
        median_context = ""
        if median_value_info:
            median_context = f"\n\nAdditional context (price signal): {median_value_info}\nConsider this as one factor along with product nature, quantity, and intent."

        template_to_use = custom_user_template if custom_user_template else DEFAULT_USER_TEMPLATE

        # Use template with variable substitution
        # We pass all potential variables so both default and custom templates work
        try:
            user_content = template_to_use.format(
                product=product,
                quantity=quantity,
                unit=unit,
                price_range=price_range,
                median_context=median_context,
                result_format=current_result_format
            )
        except KeyError as e:
            # Fallback for custom templates that might be missing keys or using old format
            # If the custom template fails to format, we might try a simplified version or log error
            # For now, let's assume valid custom templates or fallback to f-string construction style if needed
             user_content = f"""
Analyze this order:
- Product: {product}
- Quantity: {quantity} {unit}
- Price Info: {price_range}{median_context}

Based on intent, scale, and real-world buying behavior, is this more likely Retail or Non-Retail?

Return ONLY valid standard JSON (RFC 8259).
Do NOT wrap in markdown code blocks.
Do NOT use single quotes for keys or string values.
Format:
{current_result_format}
"""

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "system", "content": "Here are some classification examples:\n" + assistant_few_shots},
                {"role": "user", "content": user_content}
            ],
            "max_tokens": 5000,
            #"seed" : 42,
            "temperature": 0.0,
            "top_p": 0.01
        }
        
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code != 200:
                return AuditResult(
                    "Error",
                    "Low",
                    f"HTTP {response.status_code}: {response.text[:50]}"
                )
                
            response_json = response.json()
            content = response_json["choices"][0]["message"]["content"]
            
            data = self._parse_response(content)
            return AuditResult(
                classification=data.get("classification", "Unknown"),
                confidence=data.get("confidence", "Low"),
                reasoning=data.get("reasoning", "Error parsing reasoning")
            )
            
        except Exception as e:
            return AuditResult(
                classification="Error",
                confidence="Low",
                reasoning=f"Connection Error: {str(e)}"
            )

    def _parse_response(self, text: Any) -> Dict[str, Any]:
        """
        Clean and parse JSON from LLM response.
        """
        try:
            if isinstance(text, dict):
                return text
                
            if not isinstance(text, str):
                return {
                    "classification": "Error",
                    "confidence": "Low",
                    "reasoning": f"Unexpected type: {type(text)}"
                }

            # Brutal cleanup
            text = text.replace("```json", "").replace("```", "").strip()
            
            # Attempt direct parse
            try:
                return json.loads(text)
            except:
                pass

            # Extract JSON substring
            start_idx = text.find("{")
            end_idx = text.rfind("}")

            if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
                json_str = text[start_idx:end_idx + 1]
                json_str = json_str.replace("\n", " ").replace("\t", " ")
                
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError as e:
                    return {
                        "classification": "Error",
                        "confidence": "Low",
                        "reasoning": f"JSON Parse Error: {e.msg}"
                    }

            return {
                "classification": "Error",
                "confidence": "Low",
                "reasoning": f"No valid JSON found. Raw: {text[:50]}..."
            }

        except Exception as e:
            return {
                "classification": "Error",
                "confidence": "Low",
                "reasoning": f"Unexpected Error: {str(e)}"
            }
