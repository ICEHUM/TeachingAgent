"""Load the backend-only model credentials and create the OpenHands LLM."""
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from openhands.sdk import LLM


def normalize_cache_usage(response):
    """Bridge LiteLLM 1.101's removed None field and OpenHands 1.49.2 telemetry."""
    details = getattr(getattr(response, "usage", None), "prompt_tokens_details", None)
    if details is not None and not hasattr(details, "cache_creation_tokens"):
        # Preserve a real cache-write count; an absent count represents no reported writes.
        details.cache_creation_tokens = getattr(details, "cache_write_tokens", None) or 0


class TeachingLLM(LLM):
    def _validate_chat_response(self, resp, **kwargs):
        # This hook is shared by synchronous and asynchronous chat completions.
        normalize_cache_usage(resp)
        return super()._validate_chat_response(resp, **kwargs)


class ModelSettings(BaseSettings):
    llm_model: str
    llm_api_key: SecretStr
    llm_base_url: str
    llm_provider: str = "deepseek"

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )


def build_llm(**overrides):
    settings = ModelSettings()
    if not settings.llm_api_key.get_secret_value() or not settings.llm_base_url:
        raise ValueError("LLM_API_KEY and LLM_BASE_URL must be configured in backend/.env")
    name = settings.llm_model
    if "/" not in name:
        name = f"{settings.llm_provider}/{name}"
    options = dict(
        model=name,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url.rstrip("/"),
        usage_id="teaching-agent",
        timeout=60,
        num_retries=2,
        max_output_tokens=4096,
        log_completions=False,
    )
    options.update(overrides)
    return TeachingLLM(**options)
