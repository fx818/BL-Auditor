import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

from .auditor_llm import (
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_FEW_SHOTS,
    DEFAULT_USER_TEMPLATE,
    DEFAULT_RESULT_FORMAT
)

@dataclass
class PromptTemplate:
    name: str
    system_prompt: str
    few_shot_examples: str
    user_prompt_template: str
    description: str = ""
    created_at: str = ""
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

class PromptManager:
    """Manages custom prompts for the LLM auditor"""
    
    def __init__(self, storage_path: str = "data/custom_prompts.json"):
        self.storage_path = storage_path
        self._ensure_storage_exists()
    
    def _ensure_storage_exists(self):
        """Create storage file if it doesn't exist"""
        if not os.path.exists(self.storage_path):
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            self._save_prompts({})
    
    def _load_prompts(self) -> Dict[str, dict]:
        """Load all saved prompts from storage"""
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    
    def _save_prompts(self, prompts: Dict[str, dict]):
        """Save prompts to storage"""
        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(prompts, f, indent=2, ensure_ascii=False)
    
    def save_prompt(self, prompt: PromptTemplate) -> bool:
        """Save a new prompt or update existing one"""
        try:
            prompts = self._load_prompts()
            prompts[prompt.name] = asdict(prompt)
            self._save_prompts(prompts)
            return True
        except Exception as e:
            print(f"Error saving prompt: {e}")
            return False
    
    def get_prompt(self, name: str) -> Optional[PromptTemplate]:
        """Retrieve a specific prompt by name"""
        prompts = self._load_prompts()
        if name in prompts:
            return PromptTemplate(**prompts[name])
        return None
    
    def list_prompts(self) -> List[str]:
        """Get list of all saved prompt names"""
        prompts = self._load_prompts()
        return sorted(prompts.keys())
    
    def delete_prompt(self, name: str) -> bool:
        """Delete a prompt by name"""
        try:
            prompts = self._load_prompts()
            if name in prompts:
                del prompts[name]
                self._save_prompts(prompts)
                return True
            return False
        except Exception as e:
            print(f"Error deleting prompt: {e}")
            return False
    
    def get_all_prompts(self) -> Dict[str, PromptTemplate]:
        """Get all prompts as PromptTemplate objects"""
        prompts = self._load_prompts()
        return {name: PromptTemplate(**data) for name, data in prompts.items()}

def get_default_prompt() -> PromptTemplate:
    """Returns the default prompt template"""
    
    # We construct the user template by pre-filling the result format
    # so it is visible and editable in the UI
    user_prompt_template = DEFAULT_USER_TEMPLATE.replace("{result_format}", DEFAULT_RESULT_FORMAT)
    
    return PromptTemplate(
        name="Default",
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        few_shot_examples=DEFAULT_FEW_SHOTS,
        user_prompt_template=user_prompt_template,
        description="Default retail auditor prompt with balanced examples",
        created_at=datetime.now().isoformat()
    )
