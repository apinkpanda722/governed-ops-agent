import copy
import hashlib
import json
import re
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from govops.guardrails import screen, semantic
from govops.guardrails.pii import find_pii
from govops.guardrails.screen import (
    INPUT_PII_KINDS,
    QUARANTINE_MARKER,
    screen_request,
    screen_tool_messages,
    screen_user_messages,
    valid_tool_call_id,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "src/govops/guardrails/classifier_v2.json"
TEXT = "운영 업무의 진행 상황을 확인하고 조사한 내용과 다음 점검 일정을 함께 정리해 주세요."
PII = {
    "rrn": "000101-4000000",
    "mobile": "010-0000-0000",
    "landline": "02-000-0000",
    "email": "synthetic@example.invalid",
    "card": "4000 0000 0000 0002",
    "account": "123-45-6789",
}


@pytest.fixture
def config():
    return semantic.load_config()


def tool(content, call_id="call_synthetic"):
    return {"role": "tool", "content": content, "tool_call_id": call_id}


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


def test_config_structure_and_registration(config):
    assert set(config) == {
        "version",
        "model",
        "labels",
        "max_completion_tokens",
        "timeout_s",
        "connect_timeout_s",
        "concurrency",
        "round_item_limit",
        "min_item_chars",
        "item_max_chars",
        "head_chars",
        "tail_chars",
        "truncation_marker",
        "tool_notice",
        "system_prompts",
    }
    assert set(config["labels"]) == {"safe", "unsafe"}
    assert set(config["system_prompts"]) == {"user_input", "tool_result"}
    assert {
        key: value
        for key, value in config.items()
        if key not in {"labels", "system_prompts", "tool_notice"}
    } == {
        "version": 1,
        "model": "guardrail-v2",
        "max_completion_tokens": 16,
        "timeout_s": 4.0,
        "connect_timeout_s": 2.0,
        "concurrency": 8,
        "round_item_limit": 16,
        "min_item_chars": 40,
        "item_max_chars": 2500,
        "head_chars": 1600,
        "tail_chars": 900,
        "truncation_marker": "\n…(중략)…\n",
    }
    assert config["head_chars"] + config["tail_chars"] <= config["item_max_chars"]
    assert (
        semantic.config_sha256() == hashlib.sha256(CONFIG_FILE.read_bytes()).hexdigest()
    )
    registration = (ROOT / "docs/results/hypotheses-m6-guardrail-v2.md").read_text()
    values = re.findall(r"^v2_config_sha256: (.+)$", registration, re.MULTILINE)
    assert len(values) == 1
    if re.fullmatch(r"[0-9a-fA-F]{64}", values[0]):
        assert values[0] == semantic.config_sha256()
    else:
        assert values[0] == "UNREGISTERED"
        phrases = [
            *config["labels"].values(),
            *config["system_prompts"].values(),
            config["tool_notice"],
        ]
        assert len(phrases) == 5
        assert all(phrase.startswith("PLACEHOLDER_") for phrase in phrases)


@pytest.mark.parametrize("key", ["labels", "system_prompts", "version", "tool_notice"])
@pytest.mark.parametrize("mutation", ["extra", "missing", "type"])
def test_config_rejects_invalid_shape(tmp_path, config, key, mutation):
    if mutation == "extra":
        config["unknown"] = 1
    elif mutation == "missing":
        del config[key]
    else:
        config[key] = []
    path = tmp_path / "config.json"
    path.write_text(encoded(config))
    with pytest.raises(ValueError):
        semantic.load_config(path)


@pytest.mark.parametrize("key", ["labels", "system_prompts"])
@pytest.mark.parametrize("mutation", ["extra", "missing", "type"])
def test_config_rejects_invalid_nested_shape(tmp_path, config, key, mutation):
    field = next(iter(config[key]))
    if mutation == "extra":
        config[key]["unknown"] = "value"
    elif mutation == "missing":
        del config[key][field]
    else:
        config[key][field] = 1
    path = tmp_path / "config.json"
    path.write_text(encoded(config))
    with pytest.raises(ValueError):
        semantic.load_config(path)


@pytest.mark.parametrize(
    "key,value",
    [
        ("version", True),
        ("version", 2),
        ("model", None),
        ("max_completion_tokens", 16.0),
        ("timeout_s", "4.0"),
        ("timeout_s", float("inf")),
        ("connect_timeout_s", False),
        ("concurrency", True),
        ("round_item_limit", 0),
        ("min_item_chars", -1),
        ("item_max_chars", 2499),
        ("head_chars", -1),
        ("tail_chars", 901),
        ("truncation_marker", None),
        ("tool_notice", ""),
        ("labels", {"safe": "same", "unsafe": "same"}),
    ],
)
def test_config_rejects_invalid_values(tmp_path, config, key, value):
    config[key] = value
    path = tmp_path / "config.json"
    path.write_text(encoded(config))
    with pytest.raises(ValueError):
        semantic.load_config(path)


@pytest.mark.parametrize("raw", ["[]", "null", "{", '{"version": 1, "version": 1}'])
def test_config_rejects_invalid_json(tmp_path, raw):
    path = tmp_path / "config.json"
    path.write_text(raw)
    with pytest.raises(ValueError):
        semantic.load_config(path)


def test_config_hash_uses_all_bytes(tmp_path, config):
    path = tmp_path / "config.json"
    raw = encoded(config).encode()
    path.write_bytes(raw)
    first = semantic.config_sha256(path)
    path.write_bytes(raw + b"\n")
    assert semantic.load_config(path) == config
    assert semantic.config_sha256(path) != first
    assert semantic.config_sha256(path) == hashlib.sha256(raw + b"\n").hexdigest()


@pytest.mark.parametrize("kind", sorted(INPUT_PII_KINDS))
def test_v1_blocked_user_is_not_selected(config, kind):
    request = TEXT + " 합성 형식: " + PII[kind]
    assert (
        screen_user_messages([{"role": "user", "content": request}]).action == "block"
    )
    assert semantic.select_user_item(request, config) is None


def test_short_user_and_frozen_items(config):
    item = semantic.select_user_item("업무 요약", config)
    assert item == semantic.Item("user", "업무 요약", None, (), False)
    with pytest.raises(FrozenInstanceError):
        item.text = "수정"
    request = semantic.build_request(item, config)
    with pytest.raises(FrozenInstanceError):
        request.tool_choice = "auto"


@pytest.mark.parametrize("structured", [False, True])
def test_tool_selection_masks_all_six_pii_kinds(config, structured):
    content = TEXT + "\n" + "\n".join(PII.values())
    assert {match.kind for match in find_pii(content)} == INPUT_PII_KINDS
    messages = [tool(encoded({"내용": content}) if structured else content)]
    original = copy.deepcopy(messages)
    items, excess = semantic.select_tool_items(messages, config)
    assert len(items) == 1
    assert excess == 0
    assert not any(match.kind in INPUT_PII_KINDS for match in find_pii(items[0].text))
    assert messages == original
    assert items[0].path == (("내용",) if structured else ())
    assert items[0].message_index == 0


def test_tool_selection_uses_v1_quarantine(config, monkeypatch):
    sentence = "합성 문장이 검사에서 격리되는 경우입니다."

    def detect(text, *, scope):
        assert scope == "tool"
        start = text.find(sentence)
        return (
            []
            if start < 0
            else [SimpleNamespace(start=start, end=start + len(sentence))]
        )

    # A synthetic detector exercises the existing v1 sentence transformation.
    monkeypatch.setattr(screen, "find_injection", detect)
    messages = [tool(encoded({"내용": TEXT + "\n" + sentence + "\n" + TEXT}))]
    cleaned = screen_tool_messages(messages)
    assert cleaned.items[0].quarantined
    items, excess = semantic.select_tool_items(messages, config)
    assert len(items) == 1
    assert excess == 0
    assert items[0].text == TEXT + "\n" + QUARANTINE_MARKER + "\n" + TEXT


def test_json_selection_is_field_agnostic_and_skips_nonitems(config):
    identifiers = [
        "syn-" + "a" * 60,
        "OPS-" + "1" * 45,
        "a" * 32,
        "01000000000" + "b" * 53,
        "00000000-0000-0000-0000-000000000000",
        "2026-10-14T01:02:03.123456789012345678+00:00",
    ]
    payload = {
        "임의 필드": ["짧은 문장", *identifiers, " \n" + QUARANTINE_MARKER * 5 + "\n "],
        "다른 이름": {"중첩": [None, 123, True, TEXT]},
        TEXT: "키는 선별하지 않음",
    }
    items, excess = semantic.select_tool_items(
        [{"role": "assistant", "content": TEXT}, tool(encoded(payload))], config
    )
    assert items == [semantic.Item("tool", TEXT, 1, ("다른 이름", "중첩", 3), False)]
    assert excess == 0


def test_identifiers_excluded_before_additional_pii_mask(config, monkeypatch):
    config = {**config, "min_item_chars": 1}
    identifiers = [
        "syn-01000000000",
        "OPS-01000000000",
        "a" * 32,
        "01000000000" + "b" * 53,
        "00000000-0000-0000-0000-000000000000",
        "2026-10-14",
    ]
    called = []
    original = semantic.mask_pii

    def mask(text):
        called.append(text)
        return original(text)

    monkeypatch.setattr(semantic, "mask_pii", mask)
    items, excess = semantic.select_tool_items(
        [tool(encoded([*identifiers, TEXT]))], config
    )
    assert len(items) == 1
    assert items[0].text == TEXT
    assert excess == 0
    assert called == [TEXT]


def test_nonjson_including_broken_tool_json_is_whole_item(config):
    contents = ["짧은 업무 문장", '{"내용": "' + TEXT + "…(잘림)"]
    messages = [
        tool(content, f"call_{index}") for index, content in enumerate(contents)
    ]
    items, excess = semantic.select_tool_items(messages, config)
    assert len(items) == 2
    assert [(item.message_index, item.path, item.text) for item in items] == [
        (1, (), contents[1]),
        (0, (), contents[0]),
    ]
    assert excess == 0


@pytest.mark.parametrize("size", [2499, 2500, 2501, 5000])
@pytest.mark.parametrize("kind", ["user", "tool"])
def test_character_limit_and_truncation(config, size, kind):
    text = "가" * 1600 + "나" * (size - 1600 - 800) + "다" * 800
    assert len(text) == size
    if kind == "user":
        item = semantic.select_user_item(text, config)
        assert item is not None
    else:
        items, excess = semantic.select_tool_items(
            [tool(encoded({"내용": text}))], config
        )
        assert len(items) == 1
        assert excess == 0
        item = items[0]
    assert item.truncated == (size > 2500)
    if item.truncated:
        assert item.text == text[:1600] + config["truncation_marker"] + text[-900:]
        assert len(item.text) == 2508
    else:
        assert item.text == text


def test_masking_precedes_character_limit(config):
    text = "가" * 2487 + " " + PII["mobile"]
    assert len(text) == 2501
    items, excess = semantic.select_tool_items([tool(text)], config)
    assert len(items) == 1
    assert excess == 0
    assert items[0].text.endswith("[PII:mobile]")
    assert not items[0].truncated


def test_json_minimum_length_boundary(config):
    items, excess = semantic.select_tool_items(
        [tool(encoded(["가" * 39, "나" * 40]))], config
    )
    assert items == [semantic.Item("tool", "나" * 40, 0, (1,), False)]
    assert excess == 0


def test_round_limit_counts_excess_and_newest_messages_first(config):
    messages = [tool(encoded([TEXT + "가" * i for i in range(10)]), "call_old")]
    messages.append({"role": "user", "content": TEXT})
    messages.append(tool(encoded([TEXT + "나" * i for i in range(10)]), "call_new"))
    items, excess = semantic.select_tool_items(messages, config)
    assert len(items) == 16
    assert excess == 4
    assert [(item.message_index, item.path) for item in items] == [
        *[(2, (i,)) for i in range(10)],
        *[(0, (i,)) for i in range(6)],
    ]


def test_request_shapes_and_unwrapped_content(config):
    user = semantic.build_request(semantic.Item("user", TEXT, None, (), False), config)
    assert user.messages == [
        {"role": "system", "content": config["system_prompts"]["user_input"]},
        {"role": "user", "content": TEXT},
    ]
    assert user.tools is None
    assert user.tool_choice is None
    request = semantic.build_request(semantic.Item("tool", TEXT, 0, (), False), config)
    assert request.messages == [
        {"role": "system", "content": config["system_prompts"]["tool_result"]},
        {"role": "user", "content": config["tool_notice"]},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_v2_0",
                    "type": "function",
                    "function": {"name": "read_document", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_v2_0", "content": TEXT},
    ]
    assert valid_tool_call_id(request.messages[2]["tool_calls"][0]["id"])
    assert (
        request.messages[2]["tool_calls"][0]["id"]
        == request.messages[3]["tool_call_id"]
    )
    assert request.tools == [
        {
            "type": "function",
            "function": {
                "name": "read_document",
                "description": "도구 결과를 읽는다",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert request.tool_choice == "none"


@pytest.mark.parametrize(
    "text",
    [
        TEXT,
        TEXT + "\n" + "\n".join(PII.values()),
        TEXT + "\n" + QUARANTINE_MARKER,
        "가" * 1600 + TEXT * 80 + "다" * 900,
    ],
)
def test_host_selection_and_gateway_v1_equivalence(config, text):
    user = semantic.select_user_item(text, config)
    verdict = screen_user_messages([{"role": "user", "content": text}])
    if verdict.action == "block":
        assert user is None
    else:
        assert user is not None
        request = semantic.build_request(user, config)
        screened = screen_request(request.messages)
        assert screened.verdict.action == "allow"
        assert screened.messages == request.messages
        assert request.messages[-1]["content"] == user.text
    for content in (text, encoded({"어떤 필드": [text]})):
        items, excess = semantic.select_tool_items([tool(content)], config)
        assert len(items) == 1
        assert excess == 0
        for item in items:
            request = semantic.build_request(item, config)
            screened = screen_request(request.messages)
            assert screened.verdict.action == "allow"
            assert screened.messages == request.messages
            assert request.messages[-1]["content"] == item.text
            assert len(screened.items) == 1
            assert not screened.items[0].quarantined
            assert not screened.items[0].masked
            assert screened.messages[:2] == request.messages[:2]


@pytest.mark.parametrize("label", ["safe", "unsafe"])
@pytest.mark.parametrize("finish_reason", [None, "stop", "tool_calls"])
def test_parse_exact_labels(config, label, finish_reason):
    assert (
        semantic.parse_label(
            " \n" + config["labels"][label] + "\t", finish_reason, config
        )
        == label
    )


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "empty",
        "whitespace",
        "unknown",
        "prefix",
        "suffix",
        "two_labels",
        "case_changed",
    ],
)
def test_parse_rejects_nonexact_labels(config, case):
    safe, unsafe = config["labels"]["safe"], config["labels"]["unsafe"]
    output = {
        "missing": None,
        "empty": "",
        "whitespace": " \n",
        "unknown": "가" * 1000,
        "prefix": "가" * 1000 + safe,
        "suffix": unsafe + "나" * 1000,
        "two_labels": safe + "\n" + unsafe,
        "case_changed": safe.swapcase(),
    }[case]
    assert semantic.parse_label(output, "stop", config) is None


@pytest.mark.parametrize("label", ["safe", "unsafe"])
def test_parse_length_is_error(config, label):
    assert semantic.parse_label(config["labels"][label], "length", config) is None


def test_quarantine_only_flagged_values_without_mutation():
    raw = (
        ' {"内容" : ["\\uac00\\ub098", "'
        + TEXT
        + '", "'
        + QUARANTINE_MARKER
        + '"], "수치": 1e2} \n'
    )
    messages = [
        tool(raw),
        tool("그대로 남을 본문", "call_other"),
        tool("잘린 본문…(잘림)", "call_broken"),
        {"role": "user", "content": TEXT},
    ]
    original = copy.deepcopy(messages)
    changed, ids = semantic.apply_quarantine(
        messages,
        {
            (0, ("内容", 1)): True,
            (0, ("内容", 0)): False,
            (0, ("内容", 2)): True,
            (2, ()): True,
            (3, ()): True,
        },
    )
    assert messages == original
    assert changed is not messages
    assert changed[0]["content"] == raw.replace(
        encoded(TEXT), encoded(QUARANTINE_MARKER)
    )
    assert changed[1] == original[1]
    assert changed[2] == tool(QUARANTINE_MARKER, "call_broken")
    assert changed[3] == original[3]
    assert ids == ["call_synthetic", "call_broken"]
    again, ids = semantic.apply_quarantine(
        changed, {(0, ("内容", 1)): True, (2, ()): True}
    )
    assert again == changed
    assert ids == []


def test_quarantine_root_json_string_and_duplicate_call_ids():
    messages = [
        tool(encoded(TEXT)),
        tool(encoded([TEXT, TEXT])),
        tool(" \n" + QUARANTINE_MARKER * 4),
    ]
    changed, ids = semantic.apply_quarantine(
        messages,
        {
            (0, ()): True,
            (1, (0,)): True,
            (1, (1,)): True,
            (2, ()): True,
        },
    )
    assert changed[0]["content"] == encoded(QUARANTINE_MARKER)
    assert json.loads(changed[1]["content"]) == [QUARANTINE_MARKER, QUARANTINE_MARKER]
    assert changed[2] == messages[2]
    assert ids == ["call_synthetic"]


def test_quarantine_invalid_paths_nonstring_and_invalid_ids():
    messages = [tool(encoded({"내용": TEXT, "수치": 3}), "invalid"), tool("전체 본문")]
    changed, ids = semantic.apply_quarantine(
        messages,
        {
            (0, ("내용",)): True,
            (0, ("수치",)): True,
            (0, ("없음",)): True,
            (1, ("잘못된 경로",)): True,
            (99, ()): True,
        },
    )
    assert json.loads(changed[0]["content"]) == {"내용": QUARANTINE_MARKER, "수치": 3}
    assert changed[1] == messages[1]
    assert ids == []
