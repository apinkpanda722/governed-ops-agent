import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { RecordsView } from "./records-view";
import { testids } from "@/lib/testids";
import { readFileSync } from "node:fs";
import { auditCodeLabel } from "@/lib/messages";

const id="a".repeat(32), ts="2026-10-16T01:02:03+00:00";
const event=(seq:number, name="side_effect.attempt")=>({seq,ts,event:name,actor:"demo-platform-g3",hash12:"b".repeat(12)});
const audit={conversation_id:id,truncated:false,turns:[{turn_id:"c".repeat(32),turn_index:2,events:[event(4),event(3,"approval.approved")]},{turn_id:id,turn_index:1,events:[{...event(2,"future.event"),reason:"future_reason",decision:"future_decision",code:"future_code"},event(1,"guardrail.new_event")]}]};
const verify={status:"pass",checked_at:ts,chain:{records:4,files:1,anchors_checked:2,problems:[]},anchors:{committed:1,pending:1},legacy_unverified:{files:0,lines:0},known_limit:"tail_after_last_anchor_not_detected"};
function stub(auditStatus=200, summary:unknown=verify) {
 const fetcher=vi.fn(async(path:string)=>new Response(JSON.stringify(path.endsWith("/verify")?summary:auditStatus===200?audit:{code:"conversation_not_found"}),{status:path.endsWith("/verify")?200:auditStatus}));
 vi.stubGlobal("fetch",fetcher); return fetcher;
}
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
test.each(["pass","fail"])("records show %s badge with host and anchor limitations",async status=>{
 const fetcher=stub(200,{...verify,status,chain:{...verify.chain,problems:status==="fail"?[{code:"tail_truncated",seq:5}]:[]}});
 render(<RecordsView id={id}/>);
 await waitFor(()=>expect(screen.getByTestId(testids.recordsVerify).textContent).toContain(status.toUpperCase()));
 const badge=screen.getByTestId(testids.recordsVerify).textContent;
 expect(badge).toContain("레코드 4개");expect(badge).toContain("고정점 2개");
 expect(badge).toContain("마지막 고정점 이후는 탐지되지 않습니다");expect(badge).toContain("이 호스트의 체인 기준입니다");
 expect(fetcher.mock.calls.map(([path])=>path).sort()).toEqual([`/api/conversations/${id}/audit`,"/api/audit/verify"].sort());
 if(status==="fail")expect(badge).toContain("꼬리 잘림");
});
test("timeline orders turns and sequences, labels events and keeps unknown codes as text",async()=>{
 stub(); render(<RecordsView id={id}/>);
 await waitFor(()=>expect(screen.getAllByTestId(testids.recordsEvent)).toHaveLength(4));
 const rows=screen.getAllByTestId(testids.recordsEvent);
 expect(rows.map(row=>row.getAttribute("data-seq"))).toEqual(["1","2","3","4"]);
 expect(rows[0].textContent).toContain("Guardrail 판정");
 for(const code of ["future.event","future_reason","future_decision","future_code"])expect(rows[1].textContent).toContain(code);
 expect(rows[2].textContent).toContain("결정자");expect(rows[2].textContent).toContain("승인 완료");
});
test("404 explains cleanup and CLI verification even if global verification fails",async()=>{
 vi.stubGlobal("fetch",vi.fn(async(path:string)=>new Response(JSON.stringify({code:path.endsWith("/verify")?"upstream_unavailable":"conversation_not_found"}),{status:path.endsWith("/verify")?502:404})));
 render(<RecordsView id={id}/>);
 await waitFor(()=>expect(screen.getByTestId(testids.recordsNotFound).textContent).toBe("기록을 찾을 수 없습니다. 정리된 대화는 기록 화면에서 볼 수 없고 체인은 audit-verify로 확인합니다"));
 expect(screen.queryByTestId(testids.recordsVerify)).toBeNull();
});
test("timeline renders HTML and markdown as plain text and indicates truncation",async()=>{
 const value={...audit,truncated:true,turns:[{turn_id:id,turn_index:1,events:[{...event(1,"<b>**future**</b>"),reason:"<script>text</script>"}]}]};
 vi.stubGlobal("fetch",vi.fn(async(path:string)=>new Response(JSON.stringify(path.endsWith("/verify")?verify:value))));
 const {container}=render(<RecordsView id={id}/>);
 await waitFor(()=>expect(screen.getAllByTestId(testids.recordsEvent)).toHaveLength(1));
 expect(screen.getByTestId(testids.recordsEvent).textContent).toContain("<b>**future**</b>");
 expect(container.querySelector("script, b, strong")).toBeNull();expect(screen.getByText("이벤트가 많아 일부 기록만 표시합니다.")).toBeDefined();
});
test("verification errors never imply PASS and preserve the timeline",async()=>{
 vi.stubGlobal("fetch",vi.fn(async(path:string)=>new Response(JSON.stringify(path.endsWith("/verify")?{code:"upstream_unavailable"}:audit),{status:path.endsWith("/verify")?502:200})));
 render(<RecordsView id={id}/>);
 await waitFor(()=>expect(screen.getAllByTestId(testids.recordsEvent)).toHaveLength(4));
 expect(screen.queryByTestId(testids.recordsVerify)).toBeNull();expect(screen.getByRole("alert").textContent).toContain("검증 결과");
});
test("draft with check result, requester and decider are labeled by role",async()=>{
 const rich={...audit,turns:[{turn_id:id,turn_index:1,events:[event(1,"side_effect.draft"),{...event(2,"side_effect.draft"),check_result:"passed"},event(3,"approval.requested"),event(4,"approval.rejected")]}]};
 vi.stubGlobal("fetch",vi.fn(async(path:string)=>new Response(JSON.stringify(path.endsWith("/verify")?verify:rich),{status:200})));
 render(<RecordsView id={id}/>);
 await waitFor(()=>expect(screen.getAllByTestId(testids.recordsEvent)).toHaveLength(4));
 const rows=screen.getAllByTestId(testids.recordsEvent);
 expect(rows[0].textContent).toContain("발송 초안");expect(rows[0].textContent).not.toContain("발송 초안 검사");
 expect(rows[1].textContent).toContain("발송 초안 검사");
 expect(rows[2].textContent).toContain("요청자");expect(rows[2].textContent).not.toContain("승인자");
 expect(rows[3].textContent).toContain("결정자");
});

const toolStatuses={ok:"성공",tool_error:"도구 오류 결과",tool_not_available:"허용되지 않은 도구",invalid_argument:"인자 오류",tool_unavailable:"도구 호출 실패",proposal_invalid:"잘못된 발송 제안",not_executed:"실행하지 않음"};
test("tool call status label table defines all seven nonempty labels",()=>{
 const source=readFileSync("src/lib/messages.ts","utf8");
 const table=source.split("export function auditCodeLabel")[1].split("\n}")[0];
 const entries=[...table.matchAll(/(\w+): "([^"]+)"/g)].filter(([,code])=>code in toolStatuses);
 expect(entries.length).toBeGreaterThan(0);
 expect(Object.fromEntries(entries.map(([,code,label])=>[code,label]))).toEqual(toolStatuses);
 expect(Object.keys(toolStatuses)).toHaveLength(7);
 for(const [code,label] of Object.entries(toolStatuses))expect(auditCodeLabel(code)).toBe(label);
});
test.each(Object.entries(toolStatuses))("tool.call renders display labels for %s without hashes",async(status,label)=>{
 const hidden={args_hmac:"d".repeat(64),args_hmac_kid:"synthetic-hidden-kid",tool_call_sha256:"f".repeat(64)};
 const value={conversation_id:id,turns:[{turn_id:id,turn_index:1,events:[
 {...event(1,"tool.call"),tool_name:"search_docs",status,result_count:3,...hidden},
 {...event(2,"tool.call"),tool_name:"unknown",status:"not_executed",result_count:0,...hidden},
 ]}]};
 vi.stubGlobal("fetch",vi.fn(async(path:string)=>new Response(JSON.stringify(path.endsWith("/verify")?verify:value))));
 const {container}=render(<RecordsView id={id}/>);
 await waitFor(()=>expect(screen.getAllByTestId(testids.recordsEvent)).toHaveLength(2));
 const rows=screen.getAllByTestId(testids.recordsEvent);
 expect(rows[0].textContent).toContain("도구 호출");expect(rows[0].textContent).toContain("도구: 문서 검색");
 expect(rows[0].textContent).toContain(`결과: ${label}`);expect(rows[0].textContent).toContain("결과 수: 3");
 expect(rows[1].textContent).toContain("도구: 알 수 없는 도구");expect(rows[1].textContent).toContain("결과 수: 0");
 for(const [key,value] of Object.entries(hidden)) {expect(container.innerHTML).not.toContain(key);expect(container.innerHTML).not.toContain(value);}
});
test("tool.call without optional display fields does not render extra labels",async()=>{
 const value={turns:[{turn_id:id,turn_index:1,events:[event(1,"tool.call")]}]};
 vi.stubGlobal("fetch",vi.fn(async(path:string)=>new Response(JSON.stringify(path.endsWith("/verify")?verify:value))));
 render(<RecordsView id={id}/>);
 await waitFor(()=>expect(screen.getByTestId(testids.recordsEvent).textContent).toContain("도구 호출"));
 const row=screen.getByTestId(testids.recordsEvent).textContent;
 for(const label of ["도구:","결과:","결과 수:"])expect(row).not.toContain(label);
});
