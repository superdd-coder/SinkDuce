from __future__ import annotations

from src.config import LLMConfig, LLMProviderConfig
from src.providers.base import LLMProvider
from src.providers.registry import llm_registry

# Import adapter modules to trigger @register decorators.
# Adding a new adapter requires: (1) write a new module with @llm_registry.register("name"),
# (2) add the import here. Nothing else.
from src.providers.llm import openai_compat  # noqa: F401


def _resolve_provider_name(config: LLMProviderConfig) -> str:
    # LLMProviderConfig defaults provider="openai_compatible"; defensive fallback for legacy configs.
    return (config.provider or "openai_compatible").strip()


def create_llm_provider(config: LLMConfig | LLMProviderConfig) -> LLMProvider | None:
    """Create LLM provider from config. Returns None if no providers configured."""
    if isinstance(config, LLMConfig):
        providers = config.providers
        if not providers:
            return None
        provider_cfg = next((p for p in providers if p.is_default), providers[0])
    else:
        provider_cfg = config

    return llm_registry.create(_resolve_provider_name(provider_cfg), provider_cfg)


def create_llm_for_provider(
    provider_cfg: LLMProviderConfig,
    model: str | None = None,
) -> LLMProvider:
    """Create an LLM instance from a specific provider config with optional model override."""
    if model:
        # Apply the override by producing an updated copy of the config; adapters read it themselves.
        provider_cfg = provider_cfg.model_copy(update={"default_model": model})
    return llm_registry.create(_resolve_provider_name(provider_cfg), provider_cfg)
