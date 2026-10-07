"""One place that picks the provider. SJ_LLM_PROVIDER in .env: "groq" (default) or "gemini"."""
import os


def get_llm():
    provider = os.environ.get("SJ_LLM_PROVIDER", "groq").lower()
    if provider == "gemini":
        from sjbot.llm_gemini import GeminiChat
        return GeminiChat()
    if provider == "groq":
        from sjbot.llm_groq import GroqChat
        llm = GroqChat()
        llm.ensure_model()
        return llm

    if provider == "ollama":
        from sjbot.llm_ollama import OllamaChat
        llm = OllamaChat()
        llm.ensure_model()
        return llm
    raise RuntimeError(f"Unknown SJ_LLM_PROVIDER {provider!r} (use 'groq' or 'gemini')")