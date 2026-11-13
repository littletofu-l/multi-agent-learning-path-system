"""全局配置：全部通过环境变量注入，缺省时自动降级为内置规则引擎。"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
STATIC_DIR = PROJECT_DIR / "static"
DATA_DIR = BASE_DIR / "data"

load_dotenv(PROJECT_DIR / ".env")


class Settings:
    llm_api_key: str = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    llm_base_url: str = (os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    llm_model: str = os.getenv("LLM_MODEL") or "gpt-4o-mini"
    llm_timeout: float = float(os.getenv("LLM_TIMEOUT") or 60)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


settings = Settings()
