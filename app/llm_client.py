import json
from typing import Any

from openai import OpenAI

from app.config import Settings


class LLMClient:
    """Thin wrapper around the real provider path used by end-to-end runs."""

    def __init__(self, settings: Settings):
        settings.require_provider_key()
        self.model = settings.model
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
        )

    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("The model returned an empty response")
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("The model response was not valid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("The model response must be a JSON object")
        return value
