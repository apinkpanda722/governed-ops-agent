"""검수 카드의 인라인 스타일·스크립트. 외부 리소스(CDN·폰트·이미지)를 쓰지 않는다."""

STYLE = """
body{font-family:-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;line-height:1.6;margin:0 auto;max-width:980px;padding:1.2em;color:#1b1f23;background:#fff}
h1{font-size:1.4em}h2{font-size:1.15em;margin:.2em 0}h3{font-size:1em;margin:1em 0 .3em;color:#24468a}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #c5c9d0;padding:.35em .5em;text-align:left;overflow-wrap:anywhere;font-size:.92em}
article{border:1px solid #c5c9d0;border-radius:8px;padding:1em 1.2em;margin:1.6em 0;break-inside:avoid}
article.warn{border-color:#c0392b;border-width:2px}
.banner{background:#fdecea;color:#8a1f11;border:1px solid #e6a49b;padding:.5em .8em;border-radius:6px;margin:.6em 0}
.q{background:#f2f6fd;border-left:4px solid #4a78d0;padding:.5em .8em;margin:.3em 0}
.ok{color:#1a7f37}.ng{color:#c0392b;font-weight:600}
mark{background:#ffe58f;padding:0 .1em}mark.bad{background:#ffc9c2}
.ev{background:#fafafa;border:1px solid #e1e4e8;padding:.4em .7em;margin:.3em 0 .6em;font-size:.92em;overflow-wrap:anywhere}
.small{color:#555;font-size:.88em}.tag{display:inline-block;border:1px solid #aab;border-radius:4px;padding:0 .4em;margin-right:.3em;font-size:.82em}
.carry{background:#fff7e0;border:1px solid #e8c36a;padding:.4em .7em;border-radius:6px;margin:.5em 0}
.judge label{margin-right:1.2em}.judge input.note{width:60%}
details{margin-top:.8em}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8em;background:#f6f8fa;padding:.5em}
#controls{position:sticky;top:0;background:#fff;border-bottom:1px solid #c5c9d0;padding:.6em 0;z-index:5}
#controls input,#controls select,#controls button{font-size:.95em;margin-right:.5em}
#msg{margin:.4em 0;font-weight:600}
@media print{#controls{display:none}article{break-before:page}}
"""

SCRIPT = r"""
"use strict";
var STATUSES = ["approved", "needs_change", "rejected"];
var STATUS_LABELS = {approved: "맞음", needs_change: "고칠 것 있음", rejected: "문항 제외"};
function buildConfirmed(reviewer, reviewedAt, judgments, shaById) {
  var tasks = {};
  Object.keys(judgments).sort().forEach(function (id) {
    var j = judgments[id];
    if (!j || STATUSES.indexOf(j.status) < 0 || !shaById[id]) return;
    tasks[id] = {status: j.status, spec_sha256: shaById[id], note: String(j.note || "")};
  });
  return {reviewer: String(reviewer).trim(), reviewed_at: String(reviewedAt), tasks: tasks};
}
function seedJudgments(infos, prior) {
  // 처음 열 때의 판정: 해시가 같은 기존 확정본, 그리고 요청 문장만 바뀐 문항의 이전 판정(브라우저 저장분이
  // 있으면 그것이 더 최근이므로 우선). 이어받은 것은 carried 표시가 붙고 사람이 확인해야 사라진다.
  var out = {};
  var old = (prior && prior.judgments) || {};
  infos.forEach(function (i) {
    if (i.confirmed) { out[i.id] = {status: i.confirmed, note: "", imported: true}; return; }
    if (!i.carry) return;
    var p = old[i.id];
    if (p && STATUSES.indexOf(p.status) >= 0) { out[i.id] = {status: p.status, note: p.note || "", carried: true}; return; }
    if (i.carryStatus) out[i.id] = {status: i.carryStatus, note: i.carryNote || "", carried: true};
  });
  return out;
}
function validateHeader(reviewer, reviewedAt) {
  if (!String(reviewer).trim()) return "검수자 이름을 입력하세요.";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(reviewedAt))) return "검수 날짜(YYYY-MM-DD)를 입력하세요.";
  return "";
}
if (typeof globalThis !== "undefined") globalThis.__cards = {buildConfirmed: buildConfirmed, validateHeader: validateHeader, seedJudgments: seedJudgments};
if (typeof document !== "undefined") (function () {
  var memory = {};
  var key = document.body.getAttribute("data-storage-key") || "govops-review";
  function load() {
    try { var raw = window.localStorage.getItem(key); if (raw) return JSON.parse(raw); } catch (e) {}
    return memory.state || null;
  }
  function persist() {
    memory.state = state;
    try { window.localStorage.setItem(key, JSON.stringify(state)); } catch (e) {}
  }
  var articles = Array.prototype.slice.call(document.querySelectorAll("article[data-task]"));
  var sha = {};
  articles.forEach(function (a) { sha[a.getAttribute("data-task")] = a.getAttribute("data-sha"); });
  var state = load();
  if (!state || typeof state !== "object") {
    var prior = null;
    try { var pk = document.body.getAttribute("data-prev-key"); if (pk) prior = JSON.parse(window.localStorage.getItem(pk) || "null"); } catch (e) {}
    var infos = articles.map(function (a) {
      return {id: a.getAttribute("data-task"), confirmed: a.getAttribute("data-confirmed") || "",
        carry: a.getAttribute("data-carry") === "1", carryStatus: a.getAttribute("data-carry-status") || "",
        carryNote: a.getAttribute("data-carry-note") || ""};
    });
    state = {reviewer: (prior && prior.reviewer) || "", reviewed_at: (prior && prior.reviewed_at) || "", judgments: seedJudgments(infos, prior)};
  }
  var $ = function (id) { return document.getElementById(id); };
  if (!state.reviewed_at) state.reviewed_at = new Date().toISOString().slice(0, 10);
  $("reviewer").value = state.reviewer || "";
  $("reviewed_at").value = state.reviewed_at;
  function judged() { return Object.keys(state.judgments).filter(function (id) { return sha[id] && STATUSES.indexOf(state.judgments[id].status) >= 0; }); }
  function refresh() {
    articles.forEach(function (a) {
      var id = a.getAttribute("data-task"), j = state.judgments[id];
      a.querySelectorAll("input[type=radio]").forEach(function (r) { r.checked = !!j && r.value === j.status; });
      var note = a.querySelector("input.note"); if (note && document.activeElement !== note) note.value = j ? (j.note || "") : "";
      var cell = document.getElementById("st-" + id);
      if (cell) cell.textContent = j && j.status ? STATUS_LABELS[j.status] + (j.imported ? " (기존 확정본)" : "") + (j.carried ? " (이전 판정 이어받음 — 문장 확인 전)" : "") : "미판정";
    });
    var pending = judged().filter(function (id) { return state.judgments[id].carried; }).length;
    $("progress").textContent = judged().length + " / " + articles.length + " 문항 판정" + (pending ? " (문장 확인 전 " + pending + "개)" : "");
    var t = $("f-type").value, s = $("f-split").value, e = $("f-exec").value, only = $("f-pending").checked, warn = $("f-warn").checked, carry = $("f-carry").checked;
    articles.concat(Array.prototype.slice.call(document.querySelectorAll("tr[data-task]"))).forEach(function (el) {
      var id = el.getAttribute("data-task"), j = state.judgments[id];
      var hide = (t && el.getAttribute("data-type") !== t) || (s && el.getAttribute("data-split") !== s) ||
        (e && el.getAttribute("data-exec") !== e) || (only && j && j.status) || (warn && el.getAttribute("data-warn") !== "1") ||
        (carry && el.getAttribute("data-carry") !== "1");
      el.hidden = !!hide;
    });
  }
  document.addEventListener("click", function (ev) {
    var el = ev.target;
    if (el.matches && el.matches("button.seen")) {
      var j = state.judgments[el.getAttribute("data-task")];
      if (j) { delete j.carried; persist(); refresh(); }
    }
  });
  document.addEventListener("change", function (ev) {
    var el = ev.target, id = el.getAttribute("data-task");
    if (el.matches("input[type=radio][data-task]")) {
      state.judgments[id] = {status: el.value, note: (state.judgments[id] || {}).note || ""};
      persist(); refresh();
    } else if (el.id === "reviewer" || el.id === "reviewed_at") {
      state.reviewer = $("reviewer").value; state.reviewed_at = $("reviewed_at").value; persist();
    } else if (el.matches("select,input[type=checkbox]")) { refresh(); }
  });
  document.addEventListener("input", function (ev) {
    var el = ev.target;
    if (el.matches("input.note[data-task]")) {
      var id = el.getAttribute("data-task"), j = state.judgments[id] || {status: ""};
      j.note = el.value; state.judgments[id] = j; persist();
    } else if (el.id === "reviewer" || el.id === "reviewed_at") {
      state.reviewer = $("reviewer").value; state.reviewed_at = $("reviewed_at").value; persist();
    }
  });
  $("save").addEventListener("click", function () {
    var problem = validateHeader($("reviewer").value, $("reviewed_at").value);
    var ids = judged();
    if (!problem && !ids.length) problem = "판정한 문항이 없습니다.";
    if (problem) { $("msg").textContent = problem; $("msg").className = "ng"; return; }
    var data = buildConfirmed($("reviewer").value, $("reviewed_at").value, state.judgments, sha);
    var blob = new Blob([JSON.stringify(data, null, 2) + "\n"], {type: "application/json"});
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "confirmed.json";
    document.body.appendChild(a); a.click(); a.remove();
    var unseen = ids.filter(function (id) { return state.judgments[id].carried; }).length;
    $("msg").textContent = Object.keys(data.tasks).length + "개 문항의 판정을 confirmed.json으로 내려받았습니다. data/eval/review/confirmed.json에 두세요." +
      (unseen ? " (이어받은 판정 " + unseen + "개는 새 문장을 아직 확인하지 않은 상태입니다.)" : "");
    $("msg").className = "ok";
  });
  refresh();
})();
"""
