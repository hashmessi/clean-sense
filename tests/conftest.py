import pytest

PROVIDER_VARS = ["GEMINI_API_KEY", "GEMINI_MODEL", "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
                 "ANTHROPIC_API_KEY", "CLEANSENSE_MODEL", "LLM_PROVIDER"]


@pytest.fixture(autouse=True)
def _no_live_keys(monkeypatch):
    """Tests never read the laptop's .env and never see a real key in the environment."""
    monkeypatch.setenv("CLEANSENSE_NO_DOTENV", "1")
    for var in PROVIDER_VARS:
        monkeypatch.delenv(var, raising=False)
