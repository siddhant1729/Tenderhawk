"""Every LLM call goes through here (PLAN.md §4.1). Model names come from env only.

Two providers: Claude when ANTHROPIC_API_KEY is set, otherwise Groq's free tier when GROQ_API_KEY is set
(LLM_PROVIDER=anthropic|groq forces one). Callers don't know which one answered.
"""
import json
import logging
import os
from typing import TypeVar

import anthropic
import openai
from pydantic import BaseModel, ValidationError

import api.config  # noqa: F401  (loads .env before the env reads below, in every entry point)

log = logging.getLogger("api.llm")

LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
LLM_MODEL_FAST = os.getenv("LLM_MODEL_FAST", "claude-haiku-5-5")
# Extraction from one document is routine work; raise to "high" if parse quality drops.
LLM_EFFORT = os.getenv("LLM_EFFORT", "medium")
# Server-side refusal fallback (Claude API only). Haiku has no server-side fallback.
LLM_FALLBACKS = os.getenv("LLM_FALLBACKS", "true").lower() in ("1", "true", "yes")
# Groq: gpt-oss supports strict (constrained-decoding) JSON schema output.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_MODEL_FAST = os.getenv("GROQ_MODEL_FAST", "openai/gpt-oss-20b")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
# Free tier allows 8k tokens/minute, prompt + answer. A GeM bid's eligibility table is on its first pages.
GROQ_MAX_DOC_CHARS = int(os.getenv("GROQ_MAX_DOC_CHARS", "14000"))

T = TypeVar("T", bound=BaseModel)
_client: anthropic.AsyncAnthropic | None = None
_groq_client: openai.AsyncOpenAI | None = None


class LLMError(Exception):
    """The model could not produce a valid answer. Callers degrade (e.g. verdict CHECK_DOCS)."""


def provider() -> str:
    forced = os.getenv("LLM_PROVIDER", "").lower()
    if forced in ("anthropic", "groq"):
        return forced
    return "groq" if os.getenv("GROQ_API_KEY") and not os.getenv("ANTHROPIC_API_KEY") else "anthropic"


def concurrency() -> int:
    """Parallel extractions a sweep may run. Groq's free tier only fits about one document a minute."""
    return 1 if provider() == "groq" else 4


def max_doc_chars() -> int | None:
    """Longest document text worth sending, or None for no limit."""
    return GROQ_MAX_DOC_CHARS if provider() == "groq" else None


def _llm() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        # SDK retries 429/5xx/connection errors. Bounded so flaky venue wifi can't stall a sweep for minutes.
        _client = anthropic.AsyncAnthropic(timeout=120.0, max_retries=2)
    return _client


def _groq() -> openai.AsyncOpenAI:
    global _groq_client
    if _groq_client is None:
        # More retries than Claude: a 429 here usually means "wait for the next minute", and the SDK honours retry-after.
        _groq_client = openai.AsyncOpenAI(api_key=os.getenv("GROQ_API_KEY") or "missing", base_url=GROQ_BASE_URL,
                                          timeout=120.0, max_retries=5)
    return _groq_client


def _strict_schema(schema: type[BaseModel]) -> dict:
    """Pydantic JSON schema in the form strict mode accepts: every object closed and fully required."""
    def close(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for v in node.values():
                close(v)
        elif isinstance(node, list):
            for v in node:
                close(v)
        return node
    return close(schema.model_json_schema())


async def _extract_groq(system: str, text: str, schema: type[T], fast: bool) -> T:
    model = GROQ_MODEL_FAST if fast else GROQ_MODEL
    fmt = {"type": "json_schema",
           "json_schema": {"name": schema.__name__, "strict": True, "schema": _strict_schema(schema)}}
    last = "no answer"
    for attempt in (1, 2):
        try:
            resp = await _groq().chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
                response_format=fmt,
                reasoning_effort=LLM_EFFORT if LLM_EFFORT in ("low", "medium", "high") else "medium",
                max_completion_tokens=3000,
            )
        except openai.AuthenticationError as e:
            raise LLMError("GROQ_API_KEY missing or invalid") from e
        except openai.RateLimitError as e:
            raise LLMError("Groq rate limit (free tier: 8k tokens/min); try again in a minute") from e
        except openai.APIStatusError as e:
            raise LLMError(f"Groq API error {e.status_code}: {e.message}") from e
        except openai.APIConnectionError as e:
            raise LLMError("cannot reach the Groq API") from e

        msg = resp.choices[0].message
        if getattr(msg, "refusal", None):
            raise LLMError(f"model declined: {msg.refusal}")
        try:
            return schema.model_validate(json.loads(msg.content or ""))
        except (ValueError, ValidationError) as e:
            last = f"invalid answer ({type(e).__name__}, finish_reason={resp.choices[0].finish_reason})"
            log.warning("%s (attempt %d)", last, attempt)
    raise LLMError(last)


async def extract(system: str, text: str, schema: type[T], *, fast: bool = False) -> T:
    """Structured extraction: returns a validated `schema` instance or raises LLMError.

    One retry on an unusable answer (CLAUDE.md: validate, retry once, then give up).
    """
    if provider() == "groq":
        return await _extract_groq(system, text, schema, fast)
    model = LLM_MODEL_FAST if fast else LLM_MODEL
    extra = {}
    if LLM_FALLBACKS and "haiku" not in model:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    last = "no answer"
    for attempt in (1, 2):
        try:
            resp = await _llm().beta.messages.parse(
                model=model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": text}],
                output_format=schema,
                output_config={"effort": LLM_EFFORT},
                **extra,
            )
        except ValidationError as e:
            last = f"schema validation failed: {e.errors()[0]['msg']}"
            log.warning("%s (attempt %d)", last, attempt)
            continue
        except anthropic.BadRequestError as e:
            if extra and "fallback" in str(e.message).lower():
                log.warning("API rejected refusal fallbacks (%s); retrying without them", e.message)
                extra = {}
                continue
            raise LLMError(f"bad request: {e.message}") from e  # not retryable: bad request / prompt too long
        except anthropic.AuthenticationError as e:
            raise LLMError("ANTHROPIC_API_KEY missing or invalid") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("cannot reach the Anthropic API") from e
        except TypeError as e:
            if "authentication" not in str(e).lower():
                raise  # a real bug, don't disguise it
            raise LLMError("no Anthropic credentials: set ANTHROPIC_API_KEY in .env") from e

        if resp.stop_reason == "refusal":
            raise LLMError(f"model declined ({getattr(resp.stop_details, 'category', None)})")
        if resp.parsed_output is not None:
            return resp.parsed_output
        last = f"no parsed output (stop_reason={resp.stop_reason})"
        log.warning("%s (attempt %d, request %s)", last, attempt, resp._request_id)
    raise LLMError(last)
