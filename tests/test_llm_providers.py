"""Provider transports and planner retry/fail-over logic, against a scripted fake HTTP layer.
No network, no keys. Run by verify.sh."""
import json
import socket
import urllib.request

import pytest

import llm_transport
from llm_transport import (AuthError, BadRequest, Blocked, GeminiTransport, NotConfigured,
                           OpenRouterTransport, RateLimited, ServerError, Timeout, Truncated,
                           build_transports, load_env_file, to_gemini_schema)
from planner_llm import (PLAN_SCHEMA, CircuitBreaker, LLMPlanner, PlannerUnavailable,
                         validate_plan_text)
from rules import Context

PNG = b"\x89PNG\r\n\x1a\nfake"
SECRET = "SECRET-KEY-123"
GOOD = {"mess_type": "dry_debris", "action": "clean", "rung": "L1",
        "sensitive_surface": False, "reason": "gentle first"}


class Script:
    """Fake HTTP opener: returns queued (status, headers, body) tuples or raises queued exceptions."""

    def __init__(self, *items):
        self.items = list(items)
        self.requests = []

    def __call__(self, req, timeout):
        self.requests.append({
            "url": req.full_url,
            "headers": {k.lower(): v for k, v in req.headers.items()},
            "body": json.loads(req.data),
            "timeout": timeout,
        })
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def gem_ok(text, finish="STOP", **extra):
    cand = {"content": {"parts": [{"text": text}]}, "finishReason": finish}
    body = {"candidates": [cand], "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7}}
    body.update(extra)
    return (200, {}, body)


def or_ok(text, finish="stop"):
    return (200, {}, {"choices": [{"message": {"role": "assistant", "content": text},
                                   "finish_reason": finish}],
                      "usage": {"prompt_tokens": 9, "completion_tokens": 5}})


def err(status, message="nope", headers=None):
    return (status, headers or {}, {"error": {"message": message}})


def noop_sleep(_):
    pass


def planner_with(*transports, **kw):
    return LLMPlanner(transports=list(transports), sleep=kw.pop("sleep", noop_sleep), **kw)


# ---- Gemini -----------------------------------------------------------------

def test_gemini_request_shape_key_in_header_only():
    s = Script(gem_ok(json.dumps(GOOD)))
    t = GeminiTransport(api_key=SECRET, model="gemini-test", opener=s)
    reply = t.complete("sys", "user text", PNG, PLAN_SCHEMA, 1024, 8.0)
    req = s.requests[0]
    assert req["url"].endswith("/models/gemini-test:generateContent")
    assert req["headers"]["x-goog-api-key"] == SECRET
    assert SECRET not in json.dumps(req["body"])
    gc = req["body"]["generationConfig"]
    assert gc["responseMimeType"] == "application/json"
    assert gc["responseSchema"]["type"] == "OBJECT"
    assert "additionalProperties" not in json.dumps(gc["responseSchema"])
    parts = req["body"]["contents"][0]["parts"]
    assert parts[0]["inlineData"]["mimeType"] == "image/png"
    assert reply.in_tokens == 11 and reply.out_tokens == 7


def test_gemini_schema_is_uppercase_and_nullable_rung():
    schema = to_gemini_schema(PLAN_SCHEMA)
    assert schema["properties"]["mess_type"]["type"] == "STRING"
    assert schema["properties"]["rung"]["nullable"] is True
    assert None not in schema["properties"]["rung"]["enum"]
    assert "maxLength" not in json.dumps(schema)


def test_gemini_ignores_thought_parts():
    body = {"candidates": [{"content": {"parts": [
        {"text": "reasoning...", "thought": True}, {"text": json.dumps(GOOD)}]},
        "finishReason": "STOP"}]}
    s = Script((200, {}, body))
    out = GeminiTransport(api_key="k", model="m", opener=s).complete("s", "u", None, PLAN_SCHEMA, 1024, 8.0)
    assert out.text == json.dumps(GOOD)


@pytest.mark.parametrize("body,exc", [
    ({"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}, Truncated),
    ({"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]}, Blocked),
    ({"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}, Blocked),
])
def test_gemini_truncation_and_blocks_are_not_parsed_as_plans(body, exc):
    s = Script((200, {}, body))
    with pytest.raises(exc):
        GeminiTransport(api_key="k", model="m", opener=s).complete("s", "u", None, PLAN_SCHEMA, 1024, 8.0)


# ---- OpenRouter -------------------------------------------------------------

def test_openrouter_request_shape():
    s = Script(or_ok(json.dumps(GOOD)))
    t = OpenRouterTransport(api_key="ORKEY", model="vendor/model:free", opener=s)
    reply = t.complete("sys", "user", PNG, PLAN_SCHEMA, 1024, 8.0)
    req = s.requests[0]
    assert req["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert req["headers"]["authorization"] == "Bearer ORKEY"
    body = req["body"]
    assert body["provider"] == {"require_parameters": True}
    rf = body["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert body["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "ORKEY" not in json.dumps(body)
    assert reply.in_tokens == 9 and reply.out_tokens == 5


def test_openrouter_length_and_filter_are_errors():
    with pytest.raises(Truncated):
        OpenRouterTransport(api_key="k", model="m", opener=Script(or_ok("{", "length"))).complete(
            "s", "u", None, PLAN_SCHEMA, 1024, 8.0)
    with pytest.raises(Blocked):
        OpenRouterTransport(api_key="k", model="m", opener=Script(or_ok("", "content_filter"))).complete(
            "s", "u", None, PLAN_SCHEMA, 1024, 8.0)


# ---- HTTP error mapping -----------------------------------------------------

@pytest.mark.parametrize("status,exc", [
    (429, RateLimited), (401, AuthError), (403, AuthError), (400, BadRequest), (503, ServerError),
])
def test_http_status_maps_to_error_class(status, exc):
    s = Script(err(status))
    with pytest.raises(exc):
        GeminiTransport(api_key="k", model="m", opener=s).complete("s", "u", None, PLAN_SCHEMA, 1024, 8.0)


def test_retry_after_header_is_read():
    s = Script(err(429, headers={"retry-after": "2"}))
    with pytest.raises(RateLimited) as info:
        GeminiTransport(api_key="k", model="m", opener=s).complete("s", "u", None, PLAN_SCHEMA, 1024, 8.0)
    assert info.value.retry_after == 2.0


def test_secret_is_scrubbed_from_provider_error_text():
    s = Script(err(401, message=f"bad key {SECRET}"))
    with pytest.raises(AuthError) as info:
        GeminiTransport(api_key=SECRET, model="m", opener=s).complete("s", "u", None, PLAN_SCHEMA, 1024, 8.0)
    assert SECRET not in str(info.value) and "***" in str(info.value)


def test_default_opener_maps_socket_timeout(monkeypatch):
    def boom(req, timeout):
        raise socket.timeout("slow")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(Timeout):
        GeminiTransport(api_key="k", model="m").complete("s", "u", None, PLAN_SCHEMA, 1024, 8.0)


def test_missing_key_is_not_configured_not_a_request():
    with pytest.raises(NotConfigured):
        GeminiTransport(api_key="", model="m", opener=Script()).complete("s", "u", None, PLAN_SCHEMA, 1024, 8.0)


# ---- planner: retries, fail-over, budget, validation ------------------------

def test_planner_happy_path_records_provider_and_key_free_log():
    planner = planner_with(GeminiTransport(api_key=SECRET, model="m", opener=Script(gem_ok(json.dumps(GOOD)))))
    plan = planner("dry_debris", False, Context())
    assert plan["rung"] == "L1"
    assert planner.provider_used == "gemini"
    assert planner.calls_log[0]["provider"] == "gemini"
    assert SECRET not in json.dumps(planner.calls_log)


def test_transient_503_is_retried_then_succeeds():
    s = Script(err(503), gem_ok(json.dumps(GOOD)))
    slept = []
    planner = planner_with(GeminiTransport(api_key="k", model="m", opener=s), sleep=slept.append)
    assert planner("dry_debris", False, Context())["rung"] == "L1"
    assert planner.calls == 2 and slept == [0.5]


def test_long_rate_limit_wait_fails_over_without_sleeping():
    gemini = GeminiTransport(api_key="k", model="m", opener=Script(err(429, headers={"retry-after": "60"})))
    openrouter = OpenRouterTransport(api_key="k", model="x/y:free", opener=Script(or_ok(json.dumps(GOOD))))
    slept = []
    planner = planner_with(gemini, openrouter, sleep=slept.append)
    assert planner("dry_debris", False, Context())["rung"] == "L1"
    assert planner.provider_used == "openrouter" and slept == []


def test_blocked_primary_falls_back_to_secondary():
    gemini = GeminiTransport(api_key="k", model="m",
                             opener=Script((200, {}, {"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]})))
    openrouter = OpenRouterTransport(api_key="k", model="x/y:free", opener=Script(or_ok(json.dumps(GOOD))))
    planner = planner_with(gemini, openrouter)
    assert planner("dry_debris", False, Context())["rung"] == "L1"
    assert planner.provider_used == "openrouter"


def test_all_providers_fail_reports_last_reason():
    gemini = GeminiTransport(api_key="k", model="m", opener=Script(err(401)))
    openrouter = OpenRouterTransport(api_key="k", model="x/y:free", opener=Script(err(401)))
    with pytest.raises(PlannerUnavailable) as info:
        planner_with(gemini, openrouter)("dry_debris", False, Context())
    assert info.value.reason == "auth_error"


def test_schema_violation_gets_one_repair_then_succeeds():
    bad = dict(GOOD, mess_type="bogus")
    s = Script(gem_ok(json.dumps(bad)), gem_ok(json.dumps(GOOD)))
    planner = planner_with(GeminiTransport(api_key="k", model="m", opener=s))
    assert planner("dry_debris", False, Context())["rung"] == "L1"
    assert planner.calls == 2
    assert "invalid" in s.requests[1]["body"]["contents"][0]["parts"][-1]["text"]


def test_extra_keys_are_refused_by_schema():
    with pytest.raises(Exception):
        validate_plan_text(json.dumps(dict(GOOD, sneaky="yes")))


def test_call_cap_stops_retries_with_reason_cap():
    s = Script(err(503), err(503), err(503))
    planner = planner_with(GeminiTransport(api_key="k", model="m", opener=s), max_http_calls=2)
    with pytest.raises(PlannerUnavailable) as info:
        planner("dry_debris", False, Context())
    assert info.value.reason == "cap" and planner.calls == 2


def test_no_providers_is_not_configured():
    with pytest.raises(PlannerUnavailable) as info:
        planner_with()("dry_debris", False, Context())
    assert info.value.reason == "not_configured"


def test_breaker_opens_after_repeated_failures():
    s = Script(err(401), err(401))
    breaker = CircuitBreaker(planner_with(GeminiTransport(api_key="k", model="m", opener=s)))
    for _ in range(2):
        with pytest.raises(PlannerUnavailable):
            breaker("dry_debris", False, Context())
    assert breaker.ai_offline


# ---- configuration ----------------------------------------------------------

def test_build_transports_puts_primary_first_and_skips_unconfigured(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    monkeypatch.setenv("OPENROUTER_MODEL", "vendor/model:free")
    names = [t.name for t in build_transports("openrouter")]
    assert names == ["openrouter", "gemini"]


def test_openrouter_needs_a_pinned_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    assert build_transports("openrouter") == []


def test_no_keys_means_no_transports():
    assert build_transports("gemini") == []


def test_env_file_does_not_override_real_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("CLEANSENSE_NO_DOTENV", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "real")
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=from-file\nOPENROUTER_MODEL=vendor/x:free\n", encoding="utf-8")
    load_env_file(env)
    import os
    assert os.environ["GEMINI_API_KEY"] == "real"
    assert os.environ.get("OPENROUTER_MODEL") == "vendor/x:free"
