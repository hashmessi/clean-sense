"""Run this yourself: python check_providers.py
Reads GEMINI_API_KEY / OPENROUTER_API_KEY from .env (via python-dotenv) and lists
the models each key can actually use. Prints no key material.
"""
import json
import os
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("pip install python-dotenv first, or export the vars manually.")


def get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


gkey = os.environ.get("GEMINI_API_KEY", "")
okey = os.environ.get("OPENROUTER_API_KEY", "")

print("=== Gemini ===")
if not gkey:
    print("no GEMINI_API_KEY set")
else:
    try:
        gem = get(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
            {"x-goog-api-key": gkey},
        )
        for m in gem.get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                print(m["name"].replace("models/", ""),
                      "| in:", m.get("inputTokenLimit"),
                      "| out:", m.get("outputTokenLimit"))
    except Exception as e:
        print("ERROR:", e)

print()
print("=== OpenRouter key status ===")
if not okey:
    print("no OPENROUTER_API_KEY set")
else:
    try:
        key = get("https://openrouter.ai/api/v1/key", {"Authorization": f"Bearer {okey}"})
        d = key.get("data", {})
        print({k: d.get(k) for k in ("label", "is_free_tier", "limit", "limit_remaining", "rate_limit")})
    except Exception as e:
        print("ERROR:", e)

    print()
    print("=== OpenRouter free vision models ===")
    try:
        models = get("https://openrouter.ai/api/v1/models", {"Authorization": f"Bearer {okey}"})
        free = [m for m in models["data"] if m["id"].endswith(":free")]
        print("total free models:", len(free))
        for m in free:
            mods = m.get("architecture", {}).get("input_modalities", [])
            if "image" in mods:
                print("VISION-FREE", m["id"], "| ctx", m.get("context_length"))
    except Exception as e:
        print("ERROR:", e)
