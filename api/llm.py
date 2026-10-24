"""Every LLM call goes through here (PLAN.md §4.1). Model names come from env only."""
import logging
import os
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

log = logging.getLogger("api.llm")

LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
LLM_MODEL_FAST = os.getenv("LLM_MODEL_FAST", "claude-haiku-5-5")
# Extraction from one document is routine work; raise to "high" if parse quality drops.
LLM_EFFORT = os.getenv("LLM_EFFORT", "medium")
# Server-side refusal fallback (Claude API only). Haiku has no server-side fallback.
LLM_FALLBACKS = os.getenv("LLM_FALLBACKS", "true").lower() in ("1", "true", "yes")

T = TypeVar("T", bound=BaseModel)
_client: anthropic.AsyncAnthropic | None = None


class LLMError(Exception):
    """The model could not produce a valid answer. Callers degrade (e.g. verdict CHECK_DOCS)."""


def _llm() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(max_retries=3)  # SDK retries 429/5xx/connection errors
    return _client


async def extract(system: str, text: str, schema: type[T], *, fast: bool = False) -> T:
    """Structured extraction: returns a validated `schema` instance or raises LLMError.

    One retry on an unusable answer (CLAUDE.md: validate, retry once, then give up).
    """
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
        except anthropic.BadRequestError as e:  # not retryable: bad request / prompt too long
            raise LLMError(f"bad request: {e.message}") from e
        except anthropic.AuthenticationError as e:
            raise LLMError("ANTHROPIC_API_KEY missing or invalid") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("cannot reach the Anthropic API") from e
        except TypeError as e:  # raised by the SDK when no credentials are configured at all
            raise LLMError(f"LLM client not configured: {e}") from e

        if resp.stop_reason == "refusal":
            raise LLMError(f"model declined ({getattr(resp.stop_details, 'category', None)})")
        if resp.parsed_output is not None:
            return resp.parsed_output
        last = f"no parsed output (stop_reason={resp.stop_reason})"
        log.warning("%s (attempt %d, request %s)", last, attempt, resp._request_id)
    raise LLMError(last)
