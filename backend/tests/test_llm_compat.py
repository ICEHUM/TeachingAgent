from types import SimpleNamespace
from litellm.types.utils import PromptTokensDetailsWrapper
from app.agent.llm import normalize_cache_usage


def test_missing_cache_write_count_does_not_lose_cached_reads():
    details = PromptTokensDetailsWrapper(cached_tokens=42)
    normalize_cache_usage(SimpleNamespace(usage=SimpleNamespace(prompt_tokens_details=details)))
    assert details.cache_creation_tokens == 0
    assert details.cached_tokens == 42


def test_real_cache_write_count_is_preserved():
    details = PromptTokensDetailsWrapper(cached_tokens=42, cache_creation_tokens=17)
    normalize_cache_usage(SimpleNamespace(usage=SimpleNamespace(prompt_tokens_details=details)))
    assert details.cache_creation_tokens == 17
    assert details.cached_tokens == 42


def test_no_usage_is_supported():
    normalize_cache_usage(SimpleNamespace(usage=None))
