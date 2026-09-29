"""OpenRouter image-only requests and strict validation; no response repair or retries."""

import base64
import json
import math
import time
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator

from .store import dumps

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_PROMPT = """Judge this photo in isolation for a family photo book, using only visible
content and no personal or external context.

Assess its potential after ordinary, non-generative corrections that could
reasonably be automated. Do not assume blur, lost detail or missing content
can be recovered. Be conservative when uncertain.

Consider emotional or narrative interest, composition and technical quality.

Return only JSON matching the response schema:
- album_score: integer 0–100, higher means more worth keeping.
  Anchors: 0 = discard; 33 = weak, useful only for context;
  67 = good album photo; 100 = strong, almost certain to keep.
- reason: briefly justify the score; mention assumed corrections or
  uncertainty when relevant."""
DEFAULT_SCHEMA = {
    "type": "object",
    "properties": {
        "album_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "reason": {"type": "string"},
    },
    "required": ["album_score", "reason"],
    "additionalProperties": False,
}


def reject_constant(value: str):
    raise ValueError(f"Non-finite JSON constant: {value}")


def unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(content: str):
    return json.loads(content, parse_constant=reject_constant, object_pairs_hook=unique_object)


def numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_config(config: dict) -> None:
    if not config["model"].strip() or not config["prompt"].strip():
        raise ValueError("Model and prompt must not be empty")
    Draft202012Validator.check_schema(config["schema"])
    if config["schema"].get("type") != "object":
        raise ValueError("The output schema must describe a JSON object")

    def check_references(value):
        if isinstance(value, dict):
            for field, item in value.items():
                if field in ("$ref", "$dynamicRef") and not item.startswith("#"):
                    raise ValueError(
                        "Schemas must be self-contained; external references are unsupported"
                    )
                check_references(item)
        elif isinstance(value, list):
            for item in value:
                check_references(item)

    check_references(config["schema"])
    if not config["score_field"]:
        raise ValueError("A score field is required")
    if config["proxy_size"] < 1 or config["max_tokens"] < 1 or config["concurrency"] < 1:
        raise ValueError("Proxy size, max output tokens and concurrency must be positive")
    if config["temperature"] is not None and (
        not numeric(config["temperature"]) or not 0 <= config["temperature"] <= 2
    ):
        raise ValueError("Temperature must be finite and between 0 and 2")
    if not numeric(config["timeout_seconds"]) or config["timeout_seconds"] <= 0:
        raise ValueError("Timeout must be finite and positive")
    prices = (config.get("input_usd_per_million"), config.get("output_usd_per_million"))
    if (prices[0] is None) != (prices[1] is None):
        raise ValueError("Provide both input and output prices, or neither")
    if any(price is not None and (not numeric(price) or price < 0) for price in prices):
        raise ValueError("Token prices must be finite and nonnegative")


def response_format(schema: dict) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "photo_judgement",
            "strict": True,
            "schema": schema,
        },
    }


def make_request(config: dict, proxy: Path) -> dict:
    encoded = base64.b64encode(proxy.read_bytes()).decode("ascii")
    body = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": config["prompt"]},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    },
                ],
            }
        ],
        "response_format": response_format(config["schema"]),
        "max_tokens": config["max_tokens"],
        "stream": False,
        # Technical capability routing only. Privacy is guaranteed by the user's key.
        "provider": {"require_parameters": True},
    }
    if config["temperature"] is not None:
        body["temperature"] = config["temperature"]
    return body


def accounting(body: dict, config: dict) -> dict:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        usage = None
    usage = usage or {}

    def tokens(key: str):
        value = usage.get(key)
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        )

    input_tokens, output_tokens = tokens("prompt_tokens"), tokens("completion_tokens")
    cost = usage.get("cost")
    cost_source = None
    if numeric(cost) and cost >= 0:
        cost_source = "provider"
    elif (
        input_tokens is not None
        and output_tokens is not None
        and config.get("input_usd_per_million") is not None
    ):
        cost = (
            input_tokens * config["input_usd_per_million"]
            + output_tokens * config["output_usd_per_million"]
        ) / 1_000_000
        cost_source = "token_price_estimate"
    else:
        cost = None
    return {
        "usage_json": dumps(usage) if usage else None,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost,
        "cost_source": cost_source,
    }


async def evaluate_image(client: httpx.AsyncClient, key: str, config: dict, proxy: Path) -> dict:
    started = time.perf_counter()
    result = {
        "status": "provider_error",
        "output_json": None,
        "raw_response": None,
        "score": None,
        "usage_json": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "cost_source": None,
        "error": None,
        "abort_run": False,
    }
    try:
        body = make_request(config, proxy)
        response = await client.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=config["timeout_seconds"],
        )
        result["raw_response"] = response.text.replace(key, "[REDACTED]") if key else response.text
        # Authentication, credit and endpoint failures cannot be fixed by another image.
        result["abort_run"] = response.status_code in (401, 402, 404)
        try:
            payload = parse_json(response.text)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"HTTP {response.status_code}: invalid provider envelope: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError("Provider response envelope is not an object")
        result.update(accounting(payload, config))
        if response.is_error or payload.get("error"):
            raise ValueError(f"HTTP {response.status_code}: {dumps(payload.get('error', payload))}")
        try:
            choice = payload["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (KeyError, TypeError, IndexError) as exc:
            raise ValueError("Provider response has no assistant completion") from exc
        if finish_reason in ("error", "content_filter"):
            raise ValueError(f"Provider/model failure (finish_reason={finish_reason!r})")
        result["status"] = "validation_error"
        if finish_reason != "stop":
            raise ValueError(f"Incomplete or rejected completion (finish_reason={finish_reason!r})")
        if not isinstance(content, str):
            raise ValueError("Assistant content must be a JSON string")
        output = parse_json(content)
        result["output_json"] = dumps(output)
        Draft202012Validator(config["schema"]).validate(output)
        score = output
        for component in config["score_field"].split("."):
            score = score[component]
        if not numeric(score):
            raise ValueError("Ranking field must be a finite number")
        result.update(status="ok", score=float(score))
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if key:
            for field in ("error", "output_json", "usage_json"):
                if isinstance(result[field], str):
                    result[field] = result[field].replace(key, "[REDACTED]")
        result["latency_ms"] = (time.perf_counter() - started) * 1000
    return result
