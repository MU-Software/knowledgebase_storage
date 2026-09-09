from backend.summarizers.anthropic import AnthropicSummarizer
from backend.summarizers.base import Summarizer
from backend.summarizers.llama import LlamaCppSummarizer

__all__ = ["AnthropicSummarizer", "LlamaCppSummarizer", "Summarizer", "build_summarizer"]


def build_summarizer(provider: object, language: str) -> Summarizer:
    from backend.models import ProviderKind

    kind = getattr(provider, "kind", None)
    if kind == ProviderKind.ANTHROPIC:
        return AnthropicSummarizer(provider, language)  # type: ignore[arg-type]
    return LlamaCppSummarizer(provider, language)  # type: ignore[arg-type]
