"""Evidence card rendering is display-only: synthetic items, no scripts, nothing hidden."""

import json

from govops.grounding import cards, prompt
from govops.grounding.evidence import EvidenceItem
from govops.grounding.sentences import build_sentences

LONG = "가" * 4000


def item(content, name="lookup_id"):
    return EvidenceItem(name, content, len(content))


def card(items, sentence="합성 문장입니다(syn-guide-001).", cited=("syn-guide-001",)):
    labels = [
        {"sample_id": "S01", "sentence_id": "s1", "sentence": sentence},
        {"sample_id": "S02", "sentence_id": "s1", "sentence": "둘째 합성입니다."},
    ]
    citations = [
        {"sample_id": "S01", "sentence_id": "s1", "doc_id": doc} for doc in cited
    ]
    return cards.render_cards(
        labels,
        citations,
        {"S01": "합성 요청", "S02": "합성 요청2"},
        {"S01": items, "S02": []},
    )


def test_one_line_json_wraps_and_documents_show_real_newlines():
    payload = {
        "documents": [
            {
                "doc_id": "syn-guide-001",
                "title": "합성 제목",
                "date": "2026-01-01",
                "is_current": True,
                "text": "첫 줄\n둘째 줄\n" + LONG,
            }
        ]
    }
    html_text = card([item(json.dumps(payload, ensure_ascii=False))])
    assert "overflow-wrap:anywhere" in html_text and "white-space:pre-wrap" in html_text
    assert "첫 줄\n둘째 줄\n" in html_text and "첫 줄\\n둘째" not in html_text
    assert "<h3>syn-guide-001 — 합성 제목" in html_text
    assert "date: 2026-01-01" in html_text and "is_current: True" in html_text
    assert "overflow-x:hidden" in html_text  # no horizontal scrolling


def test_ticket_is_a_field_value_table_and_every_field_is_shown():
    ticket = {"key": "SYN-1001", "status": "open", "labels": ["a", "b"], "body": "x\ny"}
    html_text = card([item(json.dumps(ticket), "jira_get_ticket")])
    assert "<table class='kv'>" in html_text
    for field in ("key", "status", "labels", "body", "SYN-1001"):
        assert field in html_text
    assert "x\ny" in html_text


def test_truncated_or_invalid_json_falls_back_to_verbatim_text_with_badge():
    truncated = (
        '{"documents": [{"doc_id": "syn-guide-001", "text": "끊긴\n본문'
        + cards.TRUNCATED
    )
    html_text = card([item(truncated)])
    assert "잘림(" in html_text and "끊긴\n본문" in html_text
    assert "<table" not in html_text.split("<div class='right'>")[1]
    plain = card([item("그냥 텍스트\n둘째 줄")])
    assert "그냥 텍스트\n둘째 줄" in plain and "잘림(" not in plain


def test_script_strings_are_escaped_and_no_external_resources():
    html_text = card(
        [item(json.dumps({"text": "<script>alert('합성')</script>"}))],
        sentence="<img onerror=1> 합성입니다.",
    )
    assert "<script" not in html_text and "&lt;script&gt;" in html_text
    assert "<img" not in html_text
    for forbidden in ("src=", "<link", "@import", "url(", "http://", "https://"):
        assert forbidden not in html_text


def test_layout_toc_sticky_sentences_and_bold_citations():
    html_text = card([item("본문")])
    assert "href='#S01'" in html_text and "id='S01'" in html_text
    assert "position:sticky" in html_text
    assert "<strong>syn-guide-001</strong>" in html_text
    assert "E1 lookup_id" in html_text


def test_rendering_does_not_change_grader_input_bytes():
    original = json.dumps({"documents": [{"doc_id": "syn-guide-001", "text": "a\nb"}]})
    items = (item(original),)
    sentences = build_sentences("합성입니다(syn-guide-001).")
    before = prompt.build_prompt("v3.0", "요청", sentences, items)
    card(list(items))
    after = prompt.build_prompt("v3.0", "요청", sentences, items)
    assert before == after and items[0].content == original


def test_deep_nesting_does_not_fail_and_scalars_are_shown():
    nested = value = {}
    for _ in range(40):
        value["x"] = {}
        value = value["x"]
    html_text = card([item(json.dumps(nested)), item(json.dumps({"n": 3, "ok": None}))])
    assert "E2 lookup_id" in html_text and "<code>3</code>" in html_text
