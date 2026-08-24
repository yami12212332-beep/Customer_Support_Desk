import os
from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel

_DETERMINISTIC_NODES = {"classifier"}
_DEFAULT_MODEL = "gemini-3.5-flash-lite"

def _default_temperature(node_name: str) -> float:
    return 0.0 if node_name in _DETERMINISTIC_NODES else 0.2

def get_llm(
        node_name: str,
        *,
        temperature: float | None = None,
        max_tokens: int = 1024,
) -> BaseChatModel:
    """
    Construct (and cache) the Gemini chat model for a given node.
 
    node_name is a free-text label ("classifier", "billing", "technical",
    ...) — used only to look up the deterministic-temperature default, not
    sent to the API. Keep these consistent with your actual node names.
 
    Caching is keyed on (temperature, max_tokens) so repeated calls within
    a process reuse the same client instead of reconnecting per request —
    matches the reasoning in connection.py for the DB pool (one long-lived
    client, not one per call).
    """
    resolved_temperature = temperature if temperature is not None else _default_temperature(node_name)
    return _get_cached_llm(resolved_temperature, max_tokens)

@lru_cache(maxsize=32)
def _get_cached_llm(temperature: float, max_tokens: int) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError(
            "GEMINI_API_KEY not set. Set it in your environment/ .env before"
            "calling get_llm()"
        )

    model = os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)
    return ChatGoogleGenerativeAI(
        model = model,
        temperature = temperature,
        max_tokens = max_tokens
    )