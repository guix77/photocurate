import asyncio
import base64
import json

import httpx
import pytest
from PIL import Image

from photocurate.provider import accounting, evaluate_image, validate_config


def completion(content, *, usage=None, finish="stop"):
    return {
        "id": "gen-test",
        "model": "example/vision-model",
        "provider": "Test Provider",
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": usage
        if usage is not None
        else {
            "prompt_tokens": 120,
            "completion_tokens": 20,
            "cost": 0.001,
        },
    }


def call(config, tmp_path, handler):
    proxy = tmp_path / "proxy.jpg"
    Image.new("RGB", (16, 12), "red").save(proxy)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await evaluate_image(client, "fake-openrouter-key", config, proxy)

    return asyncio.run(run())


def test_request_contains_pixels_and_fixed_prompt_only(config, tmp_path):
    def handler(request):
        body = json.loads(request.content)
        assert request.url == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer fake-openrouter-key"
        assert body["provider"] == {"require_parameters": True}
        assert body["response_format"]["json_schema"]["schema"] == config["schema"]
        content = body["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": config["prompt"]}
        assert len(content) == 2
        encoded = content[1]["image_url"]["url"].split(",", 1)[1]
        assert base64.b64decode(encoded) == (tmp_path / "proxy.jpg").read_bytes()
        assert str(tmp_path) not in request.content.decode()
        return httpx.Response(200, json=completion('{"album_score": 80, "reason": "Clear scene"}'))

    result = call(config, tmp_path, handler)
    assert result["status"] == "ok" and result["score"] == 80
    assert result["cost_usd"] == 0.001 and result["cost_source"] == "provider"
    assert result["input_tokens"] == 120 and result["output_tokens"] == 20
    assert result["latency_ms"] > 0
    assert json.loads(result["raw_response"])["provider"] == "Test Provider"


@pytest.mark.parametrize("temperature", [None, 0])
def test_models_without_temperature_can_route_without_weakening_parameter_requirements(
    config,
    tmp_path,
    temperature,
):
    config["temperature"] = temperature
    validate_config(config)

    def handler(request):
        body = json.loads(request.content)
        assert body["provider"] == {"require_parameters": True}
        assert body["response_format"]["type"] == "json_schema"
        if "temperature" in body:
            assert body["temperature"] == 0
            return httpx.Response(
                404,
                json={
                    "error": {
                        "code": 404,
                        "message": "No endpoints found that can handle the requested parameters",
                    }
                },
            )
        return httpx.Response(200, json=completion('{"album_score": 80, "reason": "Fixture"}'))

    result = call(config, tmp_path, handler)
    assert result["status"] == ("ok" if temperature is None else "provider_error")
    assert result["abort_run"] == (temperature is not None)


@pytest.mark.parametrize(
    "content",
    [
        '```json\n{"album_score": 80, "reason": "x"}\n```',
        '{"album_score": "80", "reason": "x"}',
        '{"album_score": 101, "reason": "x"}',
        '{"album_score": 80, "reason": "x", "extra": true}',
        '{"album_score": 80, "album_score": 30, "reason": "x"}',
        '{"album_score": NaN, "reason": "x"}',
        '{"album_score": 1e999, "reason": "x"}',
        "[]",
    ],
)
def test_invalid_outputs_are_not_repaired_and_cost_is_preserved(config, tmp_path, content):
    result = call(config, tmp_path, lambda _: httpx.Response(200, json=completion(content)))
    assert result["status"] == "validation_error"
    assert result["score"] is None
    assert result["error"]
    assert result["cost_usd"] == 0.001
    assert json.loads(result["raw_response"])["choices"][0]["message"]["content"] == content


def test_truncated_completion_is_invalid_even_if_json_parses(config, tmp_path):
    result = call(
        config,
        tmp_path,
        lambda _: httpx.Response(
            200, json=completion('{"album_score": 80, "reason": "x"}', finish="length")
        ),
    )
    assert result["status"] == "validation_error"
    assert "finish_reason" in result["error"]


@pytest.mark.parametrize("finish", ["error", "content_filter"])
def test_model_failure_is_distinct_from_invalid_schema(config, tmp_path, finish):
    result = call(
        config, tmp_path, lambda _: httpx.Response(200, json=completion("", finish=finish))
    )
    assert result["status"] == "provider_error" and result["cost_usd"] == 0.001


def test_provider_errors_and_timeouts_are_recorded_without_retry(config, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"message": "Rate limited", "code": 429}})

    result = call(config, tmp_path, handler)
    assert result["status"] == "provider_error"
    assert len(calls) == 1 and "429" in result["error"]
    assert result["cost_usd"] is None

    def timeout(request):
        raise httpx.ReadTimeout("Network timeout", request=request)

    result = call(config, tmp_path, timeout)
    assert result["status"] == "provider_error"
    assert "ReadTimeout" in result["error"]


def test_key_never_persisted_if_provider_echoes_it(config, tmp_path):
    result = call(
        config,
        tmp_path,
        lambda _: httpx.Response(
            401,
            json={
                "error": {"message": "Invalid fake-openrouter-key"},
                "usage": {"diagnostic": "fake-openrouter-key"},
            },
        ),
    )
    assert "fake-openrouter-key" not in json.dumps(result)
    assert "[REDACTED]" in result["raw_response"]


def test_cost_estimates_require_both_prices_and_token_counts(config):
    config.update(input_usd_per_million=2, output_usd_per_million=6)
    usage = {"usage": {"prompt_tokens": 1000, "completion_tokens": 100}}
    result = accounting(usage, config)
    assert result["cost_usd"] == 0.0026
    assert result["cost_source"] == "token_price_estimate"
    assert accounting({}, config)["cost_usd"] is None
    config["output_usd_per_million"] = None
    with pytest.raises(ValueError, match="both"):
        validate_config(config)


def test_custom_schema_and_score_path(config, tmp_path):
    config.update(
        schema={
            "type": "object",
            "properties": {
                "ranking": {
                    "type": "object",
                    "properties": {"value": {"type": "number"}},
                    "required": ["value"],
                }
            },
            "required": ["ranking"],
        },
        score_field="ranking.value",
    )
    validate_config(config)
    result = call(
        config,
        tmp_path,
        lambda _: httpx.Response(200, json=completion('{"ranking": {"value": 0.75}}')),
    )
    assert result["status"] == "ok" and result["score"] == 0.75


def test_schema_must_be_self_contained(config):
    config["schema"] = {"type": "object", "$ref": "https://example.com/schema.json"}
    with pytest.raises(ValueError, match="self-contained"):
        validate_config(config)
