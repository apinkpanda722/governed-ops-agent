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
