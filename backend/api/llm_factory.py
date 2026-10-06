"""
Unified LLM Client Factory.
Creates the appropriate client based on model name.
Supports: DeepSeek
"""

import logging

from backend.api.deepseek import DeepSeekClient
from backend import config

logger = logging.getLogger(__name__)

# Model registry: model_id -> {provider, model_name, display_name}
MODEL_REGISTRY = {
    "deepseek-flash": {
        "provider": "deepseek",
        "model_name": "deepseek-flash",
        "display_name": "DeepSeek-Flash",
    },
}

DEFAULT_MODEL = "deepseek-flash"


def get_model_info(model_id: str) -> dict:
    """Get model info from registry."""
    return MODEL_REGISTRY.get(model_id, MODEL_REGISTRY[DEFAULT_MODEL])


def get_available_models() -> list:
    """Get list of available models for frontend."""
    return [
        {"id": mid, "display_name": info["display_name"], "provider": info["provider"]}
        for mid, info in MODEL_REGISTRY.items()
    ]


# ===== Client Cache =====
_clients: dict = {}


def _get_deepseek_client(model_name: str = None) -> DeepSeekClient:
    """Get or create a DeepSeek client."""
    key = f"deepseek:{model_name or 'default'}"
    if key not in _clients:
        client = DeepSeekClient(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
            model=model_name
        )
        _clients[key] = client
    return _clients[key]


def get_llm_client(model_id: str = None) -> DeepSeekClient:
    """Get the LLM client for the given model_id.

    All providers use OpenAI-compatible API, so we reuse DeepSeekClient
    with different base_url, api_key, and model settings.

    Args:
        model_id: One of the keys in MODEL_REGISTRY. Defaults to DEFAULT_MODEL.

    Returns:
        A DeepSeekClient instance configured for the specified provider.
    """
    if not model_id:
        model_id = DEFAULT_MODEL

    info = get_model_info(model_id)
    provider = info["provider"]
    model_name = info["model_name"]

    if provider == "deepseek":
        return _get_deepseek_client(model_name)
    else:
        raise ValueError(f"Unknown provider: {provider}")