"""Human-facing evidence cards.

Rendering only changes how text is displayed. The strings handed to the grader come from
``EvidenceItem.content`` and are never touched here; nothing is hidden or summarised.
"""

import html
import json
import re
from collections.abc import Mapping, Sequence

from govops.grounding.evidence import EvidenceItem

TRUNCATED = "…(잘림)"
_MAX_DEPTH = 12
_DOC_HEADERS = ("title", "date", "is_current")

_STYLE = """
:root{color-scheme:light}
*{box-sizing:border-box}
html,body{margin:0;overflow-x:hidden;background:#fff;color:#1b1b1b;
font:15px/1.55 system-ui,-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}
body{padding:12px 16px 64px}
pre,.t,td,th,li,p,h1,h2,h3{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;min-width:0}
pre{margin:0;font:13px/1.55 ui-monospace,Menlo,monospace}
nav.toc{position:sticky;top:0;z-index:2;background:#fff;border-bottom:1px solid #ccc;padding:6px 0;margin-bottom:12px}
nav.toc a{display:inline-block;margin:2px 8px 2px 0;font-weight:600;text-decoration:none;color:#0b57d0}
section.card{display:grid;grid-template-columns:minmax(260px,34%) minmax(0,1fr);gap:20px;
border-top:3px solid #333;padding-top:10px;margin-top:20px}
.left{position:sticky;top:44px;align-self:start;max-height:calc(100vh - 52px);overflow-y:auto;
background:#f6f7f9;border:1px solid #d6d9de;border-radius:6px;padding:10px 12px}
.left h1{margin:0 0 4px;font-size:20px}
.req{color:#444;margin:0 0 8px}
ol.sentences{list-style:none;margin:0;padding:0}
ol.sentences li{margin:0 0 8px}
ol.sentences li.flag{background:#fff4d6;border-left:4px solid #d69e00;padding-left:6px}
.sid{display:inline-block;min-width:2.4em;font-weight:700;color:#0b57d0}
.right{min-width:0}
.ev{border:1px solid #d6d9de;border-radius:6px;margin:0 0 14px;padding:0 0 8px}
.ev>h2{margin:0;padding:6px 10px;font-size:15px;background:#eceff4;border-radius:6px 6px 0 0}
.ev>div{padding:6px 10px}
.badge{display:inline-block;margin-left:8px;padding:0 6px;border-radius:4px;font-size:12px;
background:#fde7e9;color:#8a1020;font-weight:600}
.badge.ok{background:#e3f4e6;color:#1d6b2e}
.doc{border:1px solid #c7ccd4;border-radius:6px;margin:8px 0;padding:6px 10px}
.doc>h3{margin:0 0 4px;font-size:18px}
table.kv{width:100%;table-layout:fixed;border-collapse:collapse;margin:4px 0}
table.kv th{width:28%;text-align:left;vertical-align:top;color:#444;font-weight:600;
border-top:1px solid #e1e4e8;padding:3px 6px 3px 0}
table.kv td{vertical-align:top;border-top:1px solid #e1e4e8;padding:3px 0}
ul.l{margin:0;padding-left:18px}
"""


def _e(value) -> str:
    return html.escape(str(value))


def _scalar(value) -> str:
    return f"<code>{_e(json.dumps(value, ensure_ascii=False))}</code>"


def _text(value: str) -> str:
    return f"<pre class='t'>{_e(value)}</pre>"


def _render(value, depth=0) -> str:
    if depth > _MAX_DEPTH:
        return _text(json.dumps(value, ensure_ascii=False, indent=2))
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, dict):
        return _dict(value, depth)
    if isinstance(value, list):
        if not value:
            return "<code>[]</code>"
        return (
            "<ul class='l'>"
            + "".join(f"<li>{_render(item, depth + 1)}</li>" for item in value)
            + "</ul>"
        )
    return _scalar(value)


def _table(rows) -> str:
    return (
        "<table class='kv'>"
        + "".join(f"<tr><th>{_e(key)}</th><td>{cell}</td></tr>" for key, cell in rows)
        + "</table>"
    )


def _dict(value: dict, depth: int) -> str:
    if not value:
        return "<code>{}</code>"
    if isinstance(value.get("doc_id"), str):
        return _document(value, depth)
    return _table((key, _render(item, depth + 1)) for key, item in value.items())


def _document(value: dict, depth: int) -> str:
    head = "".join(
        f"<span class='badge ok'>{_e(key)}: {_e(value[key])}</span>"
        for key in _DOC_HEADERS
        if key in value and key != "title"
    )
    title = f" — {_e(value['title'])}" if "title" in value else ""
    rest = [
        (key, _render(item, depth + 1))
        for key, item in value.items()
        if key not in {"doc_id", "text", *_DOC_HEADERS}
    ]
    body = _table(rest) if rest else ""
    text = _render(value["text"], depth + 1) if "text" in value else ""
    return f"<div class='doc'><h3>{_e(value['doc_id'])}{title}{head}</h3>{body}{text}</div>"


def render_item(item: EvidenceItem, n: int) -> str:
    """One evidence item. Unparseable or truncated content is shown verbatim."""
    name = _e(item.tool_name or "")
    truncated = item.content.endswith(TRUNCATED)
    badge = f"<span class='badge'>잘림({_e(TRUNCATED)})</span>" if truncated else ""
    try:
        parsed = None if truncated else json.loads(item.content)
    except ValueError:
        parsed = None
    body = _render(parsed) if isinstance(parsed, (dict, list)) else _text(item.content)
    return f"<div class='ev'><h2>E{n} {name}{badge}</h2><div>{body}</div></div>"


def _bold_citations(text: str, citations: Sequence[str]) -> str:
    escaped = _e(text)
    for doc in sorted(set(citations), key=len, reverse=True):
        pattern = re.compile(
            r"(?<![0-9A-Za-z-])" + re.escape(_e(doc)) + r"(?![0-9A-Za-z-])"
        )
        escaped = pattern.sub(lambda m: f"<strong>{m.group(0)}</strong>", escaped)
    return escaped


def render_cards(
    labels: Sequence[Mapping],
    citations: Sequence[Mapping],
    requests: Mapping[str, str],
    evidence: Mapping[str, Sequence[EvidenceItem]],
    marked: Mapping[str, set[str]] | None = None,
) -> str:
    """``requests``/``evidence`` are keyed by sample_id.

    ``marked`` (sample_id → sentence_ids) flags the sentences to check, e.g. the ones the
    grader called positive. It only changes how they are shown.
    """
    sample_ids = list(dict.fromkeys(row["sample_id"] for row in labels))
    cited: dict[tuple[str, str], list[str]] = {}
    for row in citations:
        cited.setdefault((row["sample_id"], row["sentence_id"]), []).append(
            row["doc_id"]
        )
    parts = [
        (
            "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>M8 근거 카드</title><style>{_STYLE}</style></head><body>"
        ),
        "<nav class='toc'>"
        + "".join(f"<a href='#{_e(s)}'>{_e(s)}</a>" for s in sample_ids)
        + "</nav>",
    ]
    for sample in sample_ids:
        parts.append(
            f"<section class='card' id='{_e(sample)}'><div class='left'>"
            f"<h1>{_e(sample)}</h1><p class='req'>{_e(requests[sample])}</p>"
            "<ol class='sentences'>"
        )
        for row in labels:
            if row["sample_id"] != sample:
                continue
            ids = cited.get((sample, row["sentence_id"]), [])
            flagged = row["sentence_id"] in (marked or {}).get(sample, set())
            badge = "<span class='badge'>확인 대상</span>" if flagged else ""
            parts.append(
                f"<li{' class="flag"' if flagged else ''}>"
                f"<span class='sid'>{_e(row['sentence_id'])}</span>{badge}"
                f"{_bold_citations(row['sentence'], ids)}</li>"
            )
        parts.append("</ol></div><div class='right'>")
        for n, item in enumerate(evidence[sample], 1):
            parts.append(render_item(item, n))
        parts.append("</div></section>")
    parts.append("</body></html>")
    return "\n".join(parts)
