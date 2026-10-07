import json

import pytest

from govops.obs import mask
from govops.obs.mask import (
    mask_attributes,
    mask_text,
    register_secret_values,
    scrub_secrets,
)


@pytest.mark.parametrize(
    "role", ["request", "answer", "draft", "tool_args", "tool_result", "unknown"]
)
def test_all_roles_remove_secrets(role):
    secret = "synthetic-" + "private" * 3
    register_secret_values([secret, "short"])
    text = (
        "Authorization: Basic abcdefgh\nBearer fake-bearer-value sk-"
        + "x" * 24
        + " eyJabc.eyJdef.signature "
        + secret
    )
    result = mask_text(text, role=role)
    assert all(
        part not in result
        for part in [secret, "abcdefgh", "fake-bearer-value", "x" * 24, "eyJabc"]
    )
    assert "short" == scrub_secrets("short")


@pytest.mark.parametrize("role", ["request", "answer", "draft"])
def test_readable_text_and_limit(role):
    text = "연락처 " + "010-" + "0" * 4 + "-" + "0" * 4 + " 확인 부탁합니다."
    assert mask_text(text, role=role) == "연락처 [PII:mobile] 확인 부탁합니다."
    assert mask_text("가" * 8001, role=role) == "가" * 8000 + "…(잘림, 원문 8001자)"


def test_tool_result_and_recursive_attributes():
    body = "가" * 400 + "BODY_SENTINEL_hidden"
    result = json.loads(
        mask_text(json.dumps({"ids": ["syn-1"], "body": body}), role="tool_result")
    )
    assert result == {"ids": ["syn-1"], "excerpt": "가" * 300 + "…(잘림)"}
    attrs = mask_attributes(
        {
            "tool.result": {"ids": ["syn-1"], "body": body},
            "nested": [{"input.request": "안녕", "X-Authorization": "anything"}],
            "count": 3,
            "myTOKEN": "anything",
        }
    )
    assert "BODY_SENTINEL" not in repr(attrs)
    assert attrs["myTOKEN"] == "[REDACTED]"
    assert attrs["nested"][0]["X-Authorization"] == "[REDACTED]"
    assert attrs["count"] == 3


def test_registry_has_safe_repr_and_no_logs(caplog, capsys):
    secret = "fake-" + "secret" * 4
    register_secret_values([secret])
    assert secret not in repr(vars(mask))
    assert secret not in caplog.text + capsys.readouterr().out


def test_tool_args_json_preserves_text():
    original = '{ "query" : "한국어 검색", "limit": 2 }'
    assert mask_text(original, role="tool_args") == original
    result = mask_text(
        '{"nested":{"api_key":"fake-private-value"},"query":"한국어"}', role="tool_args"
    )
    assert json.loads(result)["nested"]["api_key"] == "[REDACTED]"


@pytest.mark.parametrize(
    "key",
    [
        "Authorization",
        "access_token",
        "SECRET",
        "x_api_key",
        "Password",
        "sessionCookie",
    ],
)
def test_sensitive_key_values(key):
    assert (
        mask_attributes({key: ["anything", {"body": "anything"}]})[key] == "[REDACTED]"
    )


def test_attributes_every_string_and_unknown_role():
    secret = "registered-" + "fictitious" * 2
    register_secret_values([secret])
    attrs = mask_attributes(
        {
            "input.request": secret,
            "output.answer": secret,
            "output.draft": secret,
            "tool.args": json.dumps({"search": secret}),
            "tool.result": {"ids": [secret], "body": secret},
            "other": {"list": [secret]},
            secret: secret,
        }
    )
    assert secret not in json.dumps(attrs)
    assert (
        json.loads(mask_text("가" * 400 + "BODY_SENTINEL", role="unknown"))["excerpt"]
        == "가" * 300 + "…(잘림)"
    )


def test_excerpt_is_from_first_300_original_characters():
    pii = "fictitious" * 10 + "@example.invalid"
    body = (pii + " ") * 4 + "BODY_SENTINEL_hidden"
    result = json.loads(mask_text(json.dumps({"body": body}), role="tool_result"))
    assert "BODY_SENTINEL" not in result["excerpt"]


def test_excerpt_boundary_does_not_expose_partial_secret():
    secret = "boundary-" + "fictitious" * 4
    register_secret_values([secret])
    body = "가" * 290 + secret
    excerpt = json.loads(mask_text(body, role="tool_result"))["excerpt"]
    assert "boundary-" not in excerpt
    assert len(excerpt.removesuffix("…(잘림)")) <= 300


def test_unknown_attribute_uses_source_excerpt_limit():
    email = "fictitious" * 10 + "@example.invalid"
    text = (email + " ") * 4 + "BODY_SENTINEL_hidden"
    assert "BODY_SENTINEL" not in mask_attributes({"unknown": text})["unknown"]


@pytest.mark.parametrize("key", ["generation_id", "task_id", "trace_id"])
def test_random_identifier_attributes(key):
    import random
    from uuid import UUID

    # Seeded bulk inputs reproduce collisions without fixing production randomness.
    rng = random.Random(731)
    values = [
        rng.randbytes(8).hex()
        if key == "generation_id"
        else UUID(int=rng.getrandbits(128), version=4).hex
        for _ in range(10000)
    ]
    assert [mask_attributes({key: value})[key] for value in values] == values


@pytest.mark.parametrize("key", ["generation_id", "task_id", "trace_id"])
@pytest.mark.parametrize(
    "value", ["prefix-010-0000-0000", "cf0169859044bed", "CF0169859044BED2"]
)
def test_identifier_invalid_format_is_masked(key, value):
    assert "[PII:mobile]" in mask_attributes({key: value})[key]


def test_identifier_requires_known_key_and_string():
    value = "cf0169859044bed2"
    assert "[PII:mobile]" in mask_attributes({"unknown_id": value})["unknown_id"]
    assert (
        "[PII:mobile]"
        in mask_attributes({"generation_id": [value]})["generation_id"][0]
    )
    assert mask_attributes({"nested": [{"generation_id": value}]}) == {
        "nested": [{"generation_id": value}]
    }


@pytest.mark.parametrize("key", ["prompt_hash", "content_hash"])
def test_hash_identifier_format(key):
    value = "cf0169859044bed2" + "a" * 48
    assert mask_attributes({key: value})[key] == value
    for invalid in [value.upper(), value[:-1], value + "a"]:
        assert "[PII:mobile]" in mask_attributes({key: invalid})[key]


def test_deployment_identifier_registry():
    from pathlib import Path

    import yaml

    from govops.costs import DEPLOYMENT_MODELS

    configured = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "infra/litellm/config.yaml").read_text()
    )
    identifiers = {row["model_info"]["id"] for row in configured["model_list"]}
    assert set(DEPLOYMENT_MODELS) <= mask.DEPLOYMENT_IDS
    assert identifiers == mask.DEPLOYMENT_IDS
    for value in mask.DEPLOYMENT_IDS:
        assert mask_attributes({"deployment_id": value})["deployment_id"] == value
    assert (
        "[PII:mobile]"
        in mask_attributes({"deployment_id": "unknown-010-0000-0000"})["deployment_id"]
    )


def test_identifier_secrets_are_still_scrubbed():
    value = "cf0169859044bed2"
    register_secret_values([value])
    try:
        assert (
            mask_attributes({"generation_id": value})["generation_id"] == "[REDACTED]"
        )
        for secret in ["Bearer synthetic-credential", "sk-synthetic-credential"]:
            assert (
                mask_attributes({"generation_id": secret})["generation_id"]
                == "[REDACTED]"
            )
    finally:
        # Remove only this test's registered hash; do not affect other registrations.
        import hashlib

        with mask._lock:
            mask._registry[len(value)].remove(hashlib.sha256(value.encode()).digest())


def test_identifier_survives_trace_jsonl_sink(tmp_path):
    from govops.obs.jsonl import JsonlSink
    from govops.obs.trace import set_attributes, trace

    task_id = "a" * 32
    with trace(task_id, sinks=[JsonlSink(tmp_path)]):
        set_attributes(generation_id="cf0169859044bed2")
    record = json.loads((tmp_path / f"{task_id}.jsonl").read_text())
    assert record["attributes"]["generation_id"] == "cf0169859044bed2"
