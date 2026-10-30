"""Provider transports for the LLM planner. Provider-specific code lives only here.

A transport makes ONE HTTP request and returns the model's raw text. It does not
decide whether that text is a valid plan (planner_llm.py does), and it does not
retry (planner_llm.py does, under one call budget).

Keys come from the environment, loaded from a local .env on this laptop. They are
never printed, logged, returned to a browser, or written into run logs.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

ENV_FILE = Path(__file__).resolve().parent / ".env"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# UNVERIFIED default: confirm the live model ID with check_providers.py before relying on it.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
KNOWN_PROVIDERS = ("gemini", "openrouter", "anthropic")
_GEMINI_BLOCKED = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "RECITATION"}


def load_env_file(path: Path = ENV_FILE) -> None:
    """Load .env without overriding variables already set in the real environment.

    Tests set CLEANSENSE_NO_DOTENV=1 so they never read a real key from disk.
    """
    if os.environ.get("CLEANSENSE_NO_DOTENV"):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None
    if load_dotenv is not None:
        load_dotenv(path, override=False)
        return
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


# ---- errors: `reason` is a short code for logs and the dashboard ------------

class TransportError(Exception):
    reason = "provider_error"
    retryable = False

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class RateLimited(TransportError):
    reason, retryable = "rate_limited", True


class Timeout(TransportError):
    reason, retryable = "timeout", True


class ServerError(TransportError):
    reason, retryable = "provider_error", True


class AuthError(TransportError):
    reason, retryable = "auth_error", False


class BadRequest(TransportError):
    reason, retryable = "bad_request", False


class Blocked(TransportError):
    reason, retryable = "blocked", False


class Truncated(TransportError):
    reason, retryable = "truncated", False


class NotConfigured(TransportError):
    reason, retryable = "not_configured", False


@dataclass
class Reply:
    text: str
    in_tokens: int = 0
    out_tokens: int = 0


# ---- HTTP -------------------------------------------------------------------

def _json_or_text(raw: bytes) -> Any:
    try:
        return json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"raw": raw[:200].decode("utf-8", "replace")}


def _default_opener(req: urllib.request.Request, timeout: float):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, headers, _json_or_text(resp.read())
    except urllib.error.HTTPError as exc:
        headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        return exc.code, headers, _json_or_text(exc.read())
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise Timeout("request timed out") from exc
        raise ServerError("network unreachable") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise Timeout("request timed out") from exc


def _scrub(text: str, secret: str) -> str:
    out = text[:300]
    return out.replace(secret, "***") if secret else out


def _error_text(body: Any) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("status") or "")
        if isinstance(err, str):
            return err
        if "raw" in body:
            return str(body["raw"])
    return ""


def _retry_after(headers: dict) -> Optional[float]:
    try:
        raw = headers.get("retry-after")
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _post_json(opener, url: str, headers: dict, body: dict, timeout: float, secret: str) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", **headers})
    status, resp_headers, parsed = opener(req, timeout)
    if 200 <= status < 300:
        if not isinstance(parsed, dict):
            raise ServerError("reply is not a JSON object")
        return parsed
    msg = _scrub(_error_text(parsed) or f"HTTP {status}", secret)
    if status == 429:
        raise RateLimited(f"HTTP 429: {msg}", retry_after=_retry_after(resp_headers))
    if status in (401, 402, 403):
        raise AuthError(f"HTTP {status}: {msg}")
    if status in (408, 409, 425) or status >= 500:
        raise ServerError(f"HTTP {status}: {msg}")
    raise BadRequest(f"HTTP {status}: {msg}")


def _b64(png: bytes) -> str:
    return base64.standard_b64encode(png).decode("ascii")


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


# ---- schema: one JSON Schema in, provider dialects out ----------------------

def to_gemini_schema(node: Any) -> Any:
    """Convert standard JSON Schema to Gemini's OpenAPI-style subset.

    Upper-case type names, nullable instead of a "null" type, no None in enums,
    and keys Gemini does not accept are dropped. Pydantic stays the real check.
    """
    if isinstance(node, list):
        return [to_gemini_schema(x) for x in node]
    if not isinstance(node, dict):
        return node
    out: dict = {}
    for key, val in node.items():
        if key in ("additionalProperties", "$schema", "title", "maxLength"):
            continue
        if key == "type":
            types = val if isinstance(val, list) else [val]
            if "null" in types:
                out["nullable"] = True
            out["type"] = [t for t in types if t != "null"][0].upper()
            continue
        if key == "enum":
            out["enum"] = [v for v in val if v is not None]
            continue
        out[key] = to_gemini_schema(val)
    return out


# ---- transports -------------------------------------------------------------

@dataclass
class GeminiTransport:
    api_key: str
    model: str
    opener: Callable = _default_opener
    name: str = "gemini"

    def complete(self, system: str, text: str, png: Optional[bytes],
                 json_schema: dict, max_tokens: int, timeout: float) -> Reply:
        if not self.api_key or not self.model:
            raise NotConfigured("GEMINI_API_KEY or GEMINI_MODEL is not set")
        parts: list[dict] = []
        if png:
            parts.append({"inlineData": {"mimeType": "image/png", "data": _b64(png)}})
        parts.append({"text": text})
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": to_gemini_schema(json_schema),
                "maxOutputTokens": max_tokens,
                "temperature": 0,
            },
        }
        resp = _post_json(self.opener, GEMINI_URL.format(model=self.model),
                          {"x-goog-api-key": self.api_key}, body, timeout, self.api_key)
        feedback = resp.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise Blocked(f"prompt blocked: {feedback['blockReason']}")
        candidates = resp.get("candidates") or []
        if not candidates:
            raise ServerError("no candidates in reply")
        cand = candidates[0]
        finish = cand.get("finishReason")
        if finish == "MAX_TOKENS":
            raise Truncated("reply hit the token limit")
        if finish in _GEMINI_BLOCKED:
            raise Blocked(f"reply blocked: {finish}")
        content = cand.get("content") or {}
        out = "".join(p.get("text", "") for p in content.get("parts", [])
                      if isinstance(p, dict) and not p.get("thought"))
        usage = resp.get("usageMetadata") or {}
        return Reply(out, _int(usage.get("promptTokenCount")), _int(usage.get("candidatesTokenCount")))


@dataclass
class OpenRouterTransport:
    api_key: str
    model: str
    opener: Callable = _default_opener
    name: str = "openrouter"

    def complete(self, system: str, text: str, png: Optional[bytes],
                 json_schema: dict, max_tokens: int, timeout: float) -> Reply:
        if not self.api_key or not self.model:
            raise NotConfigured("OPENROUTER_API_KEY or OPENROUTER_MODEL is not set")
        content: list[dict] = []
        if png:
            content.append({"type": "image_url",
                            "image_url": {"url": "data:image/png;base64," + _b64(png)}})
        content.append({"type": "text", "text": text})
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "cleansense_plan", "strict": True,
                                                "schema": json_schema}},
            # Route only to providers that honour response_format, so the schema is not ignored.
            "provider": {"require_parameters": True},
        }
        resp = _post_json(self.opener, OPENROUTER_URL,
                          {"Authorization": f"Bearer {self.api_key}", "X-Title": "CleanSense Lite"},
                          body, timeout, self.api_key)
        choices = resp.get("choices") or []
        if not choices:
            raise ServerError("no choices in reply")
        choice = choices[0]
        finish = choice.get("finish_reason")
        if finish == "length":
            raise Truncated("reply hit the token limit")
        if finish == "content_filter":
            raise Blocked("reply blocked by content filter")
        out = (choice.get("message") or {}).get("content") or ""
        if not isinstance(out, str):
            out = ""
        usage = resp.get("usage") or {}
        return Reply(out, _int(usage.get("prompt_tokens")), _int(usage.get("completion_tokens")))


def _map_sdk_error(exc: Exception) -> TransportError:
    """Map Anthropic SDK errors by class name, so this module does not import the SDK."""
    name, msg = type(exc).__name__, str(exc)[:200]
    if isinstance(exc, TimeoutError) or "Timeout" in name:
        return Timeout(msg)
    if "RateLimit" in name:
        return RateLimited(msg)
    if "Authentication" in name or "Permission" in name:
        return AuthError(msg)
    if "BadRequest" in name or "NotFound" in name:
        return BadRequest(msg)
    if "InternalServer" in name or "Connection" in name or "Overloaded" in name:
        return ServerError(msg)
    return TransportError(msg)


@dataclass
class AnthropicTransport:
    client: Any
    model: str
    name: str = "anthropic"

    def complete(self, system: str, text: str, png: Optional[bytes],
                 json_schema: dict, max_tokens: int, timeout: float) -> Reply:
        if self.client is None:
            raise NotConfigured("ANTHROPIC_API_KEY is not set")
        content: Any = text
        if png:
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(png)}},
                {"type": "text", "text": text},
            ]
        try:
            msg = self.client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": content}], timeout=timeout)
        except Exception as exc:
            raise _map_sdk_error(exc) from exc
        out = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
        usage = getattr(msg, "usage", None)
        return Reply(out, _int(getattr(usage, "input_tokens", 0)), _int(getattr(usage, "output_tokens", 0)))


# ---- selection --------------------------------------------------------------

def _from_env(name: str, anthropic_model: str, opener: Optional[Callable]):
    kw = {} if opener is None else {"opener": opener}
    if name == "gemini":
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            return None
        model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
        return GeminiTransport(api_key=key, model=model, **kw)
    if name == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        model = os.environ.get("OPENROUTER_MODEL", "").strip()
        if not key or not model:
            return None
        if model == "openrouter/free":
            return None  # the random router is not reproducible run to run
        return OpenRouterTransport(api_key=key, model=model, **kw)
    if name == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            return None
        import anthropic
        return AnthropicTransport(client=anthropic.Anthropic(api_key=key),
                                  model=os.environ.get("CLEANSENSE_MODEL", "").strip() or anthropic_model)
    return None


def build_transports(primary: str = "", anthropic_model: str = DEFAULT_ANTHROPIC_MODEL,
                     opener: Optional[Callable] = None) -> list:
    """Configured providers in call order: LLM_PROVIDER first, then the rest.

    A provider with a missing key (or, for OpenRouter, a missing pinned model) is skipped.
    """
    order: list[str] = []
    for name in [primary.strip().lower(), *KNOWN_PROVIDERS]:
        if name in KNOWN_PROVIDERS and name not in order:
            order.append(name)
    out = []
    for name in order:
        transport = _from_env(name, anthropic_model, opener)
        if transport is not None:
            out.append(transport)
    return out
