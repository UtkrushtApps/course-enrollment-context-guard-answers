import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str
    base_url: str
    fixture_path: Path
    state_path: Path

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            fixture_path=ROOT / "fixtures" / "scenario_data.jsonl",
            state_path=ROOT / "local" / "course_agent.db",
        )

    def require_provider_key(self) -> None:
        if not self.api_key.strip():
            raise RuntimeError(
                "OPENAI_API_KEY is required for an end-to-end model run"
            )
