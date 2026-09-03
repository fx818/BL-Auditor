import json
import logging
import os
import time

import requests
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

log = logging.getLogger("bl-auditor.retail_agent_2")
_LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))


@dataclass
class AuditResult:
    classification: str  # "Retail" or "Non-Retail"
    confidence: str      # "High", "Medium", "Low"
    reasoning: str


_PROMPT_DIR = Path(__file__).parent


def _read_prompt(filename: str) -> str:
    return (_PROMPT_DIR / filename).read_text(encoding="utf-8")


DEFAULT_SYSTEM_PROMPT = _read_prompt("prompt.md")
DEFAULT_FEW_SHOTS = _read_prompt("few_shots.md")
DEFAULT_USER_TEMPLATE = _read_prompt("user_template.md")
DEFAULT_RESULT_FORMAT = _read_prompt("result_format.md")


class AuditorLLM:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "")
        self.model_name = model_name or os.getenv("LLM_MODEL", "")
        self.timeout = timeout if timeout is not None else float(os.getenv("LLM_TIMEOUT", "60"))

        if not self.api_key:
            raise RuntimeError("LLM_API_KEY is not set")
        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL is not set")
        if not self.model_name:
            raise RuntimeError("LLM_MODEL is not set")

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
            try:
                from app.services.prompt_override_service import get_active_prompt
                system_prompt = get_active_prompt("retail_agent_2")[0]
            except Exception:
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
                {"role": "user", "content": "Here are some classification examples:\n" + assistant_few_shots},
                {"role": "user", "content": user_content}
            ],
            "max_tokens": 2000,
            #"seed" : 42,
            "temperature": 0.0,
            "top_p": 0.01
        }

        last_exc: Exception | None = None
        for attempt in range(1, _LLM_MAX_RETRIES + 2):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                log.warning(
                    "retail_agent_2 LLM attempt %d/%d timed out/failed: %s",
                    attempt, _LLM_MAX_RETRIES + 1, exc,
                )
                if attempt <= _LLM_MAX_RETRIES:
                    time.sleep(2 ** (attempt - 1))
                    continue
                return AuditResult("Error", "Low", f"Connection error after {attempt} attempts: {exc}")
            except Exception as exc:
                return AuditResult("Error", "Low", f"Unexpected error: {exc}")

            if response.status_code == 429 or response.status_code >= 500:
                last_exc = RuntimeError(f"HTTP {response.status_code}")
                log.warning(
                    "retail_agent_2 LLM HTTP %d attempt %d/%d, retrying",
                    response.status_code, attempt, _LLM_MAX_RETRIES + 1,
                )
                if attempt <= _LLM_MAX_RETRIES:
                    time.sleep(2 ** (attempt - 1))
                    continue
                return AuditResult("Error", "Low", f"HTTP {response.status_code} after {attempt} attempts")

            if response.status_code != 200:
                return AuditResult("Error", "Low", f"HTTP {response.status_code}: {response.text[:50]}")

            try:
                content = response.json()["choices"][0]["message"]["content"]
                data = self._parse_response(content)
                return AuditResult(
                    classification=data.get("classification", "Unknown"),
                    confidence=data.get("confidence", "Low"),
                    reasoning=data.get("reasoning", "Error parsing reasoning"),
                )
            except (KeyError, IndexError) as exc:
                return AuditResult("Error", "Low", f"Unexpected LLM response shape: {exc}")

        return AuditResult("Error", "Low", f"LLM failed after {_LLM_MAX_RETRIES + 1} attempts: {last_exc}")

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
            except (json.JSONDecodeError, ValueError):
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
