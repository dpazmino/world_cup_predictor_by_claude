"""Base LLM agent with JSON parsing, retry, and model selection."""
from __future__ import annotations
import json
import re
import time
import anthropic

# Single shared client — initialized lazily so .env has time to load
_client: anthropic.Anthropic | None = None

def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client

FAST_MODEL = "claude-haiku-4-5-20251001"    # cheap parallel positioning calls
SMART_MODEL = "claude-sonnet-4-6"           # coach, referee, evaluator


def _extract_json(text: str) -> dict:
    """Try to parse JSON from model response, even if wrapped in markdown."""
    text = text.strip()
    # Remove ```json ... ``` fences if present
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    # Try to find first { ... } block
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        text = brace.group(0)
    return json.loads(text)


def llm_call(
    system: str,
    user: str,
    model: str = FAST_MODEL,
    max_tokens: int = 300,
    retries: int = 2,
    temperature: float = 0.7,
) -> dict:
    """
    Call the Anthropic API, return parsed JSON dict.
    Falls back to empty dict on persistent failure.
    """
    for attempt in range(retries + 1):
        try:
            response = _get_client().messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            raw = response.content[0].text
            return _extract_json(raw)
        except json.JSONDecodeError:
            if attempt == retries:
                return {}
            time.sleep(0.5)
        except anthropic.RateLimitError:
            time.sleep(2 ** attempt)
        except Exception as e:
            if attempt == retries:
                return {}
            time.sleep(1)
    return {}


def llm_text(
    system: str,
    user: str,
    model: str = SMART_MODEL,
    max_tokens: int = 200,
) -> str:
    """Call the API, return raw text (for commentary)."""
    try:
        response = _get_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.85,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text.strip()
    except Exception:
        return ""
