import unicodedata

import pytest

from govops.guardrails.pii import PiiMatch, find_pii, mask_pii


def synthetic(kind):
    return {
        "rrn": "000229" + "-" + "3" + "0" * 6,
        "mobile": "010-" + "0" * 4 + "-" + "0" * 4,
        "landline": "02-" + "0" * 4 + "-" + "0" * 4,
        "email": "synthetic@example.invalid",
        "card": "4" + "0" * 14 + "2",
        "biz_reg": "000" + "-00-" + "0" * 5,
        "passport": "Z" + "0" * 8,
    }[kind]


@pytest.mark.parametrize(
    "kind", ["rrn", "mobile", "landline", "email", "card", "biz_reg", "passport"]
)
def test_positive_and_preservation(kind):
    value = synthetic(kind)
    text = "앞 " + value + " 뒤"
    assert [m.kind for m in find_pii(text)] == [kind]
    assert mask_pii(text) == "앞 [PII:" + kind + "] 뒤"


@pytest.mark.parametrize(
    "value", ["9913321234567", "123", "abc@", "Z123", "4" + "0" * 15]
)
def test_negative(value):
    assert not find_pii(value)


def test_normalization_and_overlap():
    number = synthetic("mobile")
    full = "".join(
        chr(ord(c) + 0xFEE0) if c.isascii() and c.isdigit() else c for c in number
    ).replace("-", "−")
    assert mask_pii("앞 " + full + " 뒤") == "앞 [PII:mobile] 뒤"
    assert unicodedata.normalize("NFKC", "０") == "0"
    rrn = synthetic("rrn").replace("-", "")
    assert [m.kind for m in find_pii(rrn)] == ["rrn"]
    assert mask_pii(rrn, placeholder="<{kind}>") == "<rrn>"


def luhn_number(length):
    prefix = "8" + "0" * (length - 2)
    for check in range(10):
        value = prefix + str(check)
        digits = [int(c) for c in value[::-1]]
        total = sum(
            (2 * n - 9 if 2 * n > 9 else 2 * n) if i % 2 else n
            for i, n in enumerate(digits)
        )
        if total % 10 == 0:
            return value
    raise AssertionError("no check digit")


@pytest.mark.parametrize("length", range(13, 20))
@pytest.mark.parametrize("separator", ["", "-", " "])
def test_card_lengths(length, separator):
    value = luhn_number(length)
    grouped = separator.join(value[i : i + 4] for i in range(0, length, 4))
    assert find_pii(grouped) == [PiiMatch("card", 0, len(grouped))]


@pytest.mark.parametrize("prefix", ["010", "011", "016", "017", "018", "019"])
@pytest.mark.parametrize("separator", ["", "-", " "])
def test_mobile_formats(prefix, separator):
    value = separator.join([prefix, "0" * 4, "0" * 4])
    assert [m.kind for m in find_pii(value)] == ["mobile"]


@pytest.mark.parametrize(
    "prefix",
    [
        "02",
        "031",
        "032",
        "033",
        "041",
        "042",
        "043",
        "044",
        "051",
        "052",
        "053",
        "054",
        "055",
        "061",
        "062",
        "063",
        "064",
    ],
)
def test_landline_formats(prefix):
    assert [m.kind for m in find_pii(prefix + "-000-0000")] == ["landline"]


def test_hyphenated_rrn_is_format_based():
    value = "0" * 6 + "-" + "0" * 7
    assert [m.kind for m in find_pii(value)] == ["rrn"]
    assert "rrn" not in [m.kind for m in find_pii(value.replace("-", ""))]
