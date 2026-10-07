from dataclasses import replace

import pytest

from govops.gateway.hook_header import GuardrailInfo, format_header, parse_header


def test_roundtrip_and_untrusted_values():
    info = GuardrailInfo(
        "a" * 12, "b" * 12, team="platform", ids=("call_a", "toolu_b"), q=2
    )
    value = format_header(info)
    assert parse_header(value) == info
    for bad in (
        None,
        value + ";unknown=1",
        value.replace("v=1", "v=2"),
        value + "\r\n",
        "x" * 5000,
        value.replace("q=2", "q=-1"),
    ):
        assert parse_header(bad) is None


def test_ids_are_bounded_without_pii_detection():
    info = GuardrailInfo(
        "a" * 12,
        "b" * 12,
        q=70,
        ids=tuple(f"call_{i}" for i in range(69)) + ("invalid",),
    )
    parsed = parse_header(format_header(info))
    assert parsed.q == 70 and len(parsed.ids) == 64
    assert parsed.bad == 1 and parsed.trunc == 1
    assert parse_header(
        format_header(replace(info, ids=("toolu_01012345678",)))
    ).ids == ("toolu_01012345678",)


@pytest.mark.parametrize(
    "field,value",
    [("team", "bad\r\n"), ("pre", "bad"), ("rules", "z" * 12), ("ms", -1)],
)
def test_invalid_format_is_rejected(field, value):
    with pytest.raises(ValueError):
        format_header(replace(GuardrailInfo("a" * 12, "b" * 12), **{field: value}))
