// @vitest-environment node
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { projectConversation, projectApprovals, projectTurn, projectConversationList } from "./project";
test("allowlist preserves useful fields without internal data", () => {
 const token=randomBytes(32).toString("base64url");
 const junk={messages:token,rules:"SENTINEL-rules", reasoning:"SENTINEL-reason", stop_reason:token};
 const row={...junk,task_id:"a".repeat(32),turn_id:"a".repeat(32),status:"blocked_input",request:"SENTINEL-request",
 answer:"answer",cited_doc_ids:["syn-doc"],draft:{tool:"jira_add_comment",args:{ticket_id:"syn-ticket",body:"draft",...junk}},
 draft_sha256:"b".repeat(64),check:{status:"passed",message:"checked",...junk},expires_at:"2026-10-08T12:00:00Z",
 guardrail:{quarantined:2,quarantined_at_least:true,masked:token,...junk}};
 const view=projectConversation({conversation_id:"a".repeat(32),turn_index:1,history:[{role:"assistant",content:"previous",...junk}],turn:row,...junk});
 const approvals=projectApprovals({approvals:[{...row,history:"SENTINEL-history",requester_label:"demo-platform-g3"}]});
 for(const result of [view,approvals]) {
 const json=JSON.stringify(result); expect(json).not.toContain("SENTINEL"); expect(json.includes(token)).toBe(false);
 expect(json).toContain("draft"); expect(json).toContain("checked"); expect(json).toContain("2026-10-08T12:00:00Z");
 }
 expect(JSON.stringify(view)).toContain("answer"); expect(JSON.stringify(view)).toContain("syn-doc");
});
test("invalid types are omitted", () => {
 expect(projectConversation({conversation_id:[],turn_index:-1,history:[{role:"evil",content:"x"}],turn:{status:"unknown",answer:"x".repeat(2001),error:"bad-error"}})).toEqual({history:[],turn:{}});
});
test("projection source never names discarded fields", () => {
 const source=readFileSync(new URL("./project.ts",import.meta.url),"utf8");
 for(const name of ["masked","output_flagged","guardrail_block","stop_reason","evidence_ids","guardrail_signals","guardrail_post","observed_doc_ids","effect_result","reasoning","rules","messages"]) expect(source.includes(name)).toBe(false);
});
test("nested types and all visible contracts", () => {
 const id="a".repeat(32);
 const view=projectConversation({conversation_id:id,turn_index:2,history:[{role:["assistant"],content:"bad"},{role:"user",content:"hello"}],
 turn:{turn_id:id,status:"blocked_input",request:"hidden",block:{code:"pii_input",message:"remove"},
 guardrail:{quarantined:1,quarantined_at_least:false},error:"pii_input",effect:{state:"completed",mock_id:"mock-1"},rejected_edit:{status:"failed",message:"retry"},retry_hint:"retry"}});
 expect(view).toEqual({conversation_id:id,turn_index:2,history:[{role:"user",content:"hello"}],
 turn:{turn_id:id,status:"blocked_input",block:{code:"pii_input",message:"remove"},
 guardrail:{quarantined:1,quarantined_at_least:false},error:"pii_input",effect:{state:"completed",mock_id:"mock-1"},rejected_edit:{status:"failed",message:"retry"},retry_hint:"retry"}});
 expect(JSON.stringify(projectConversation({turn:{draft:{tool:["jira_add_comment"],args:{body:"bad"}}}}))).not.toContain("draft");
 const rows=projectApprovals({approvals:[{task_id:id,conversation_id:"bad",status:"awaiting_approval",draft:{tool:"slack_send_message",args:{channel:"team-platform",text:"hello",priority:9}},draft_sha256:"b".repeat(64)}]});
 expect(rows).toEqual({approvals:[{turn_id:id,status:"awaiting_approval",draft:{tool:"slack_send_message",args:{channel:"team-platform",text:"hello"}},draft_sha256:"b".repeat(64)}]});
});
test("missing top-level fields are not filled in", () => {
 expect(projectConversation({})).toEqual({});
 expect(projectApprovals({})).toEqual({});
});

test("requester tool steps retain only valid summaries and cap at twenty", () => {
 const good={name:"search_docs",ok:true};
 const view=projectTurn({tool_steps:[
 {...good,args:"SENTINEL",result:"SENTINEL",arguments:"SENTINEL",document_body:"SENTINEL"},
 {name:"read_ticket",ok:false},{name:"Bad-Name",ok:true},{name:"a".repeat(41),ok:true},
 {name:"search_docs",ok:"true"},null,{}, {name:"",ok:true}
 ]});
 expect(view.tool_steps).toEqual([good,{name:"read_ticket",ok:false}]);
 expect(JSON.stringify(view)).not.toContain("SENTINEL");
 expect(projectTurn({tool_steps:Array.from({length:21},()=>good)}).tool_steps).toHaveLength(20);
 expect(projectTurn({tool_steps:{}})).toEqual({});
});
test("requester approval method and effect timestamp validation", () => {
 for(const approval_method of ["approve","edit_approve"])
 expect(projectTurn({approval_method})).toEqual({approval_method});
 for(const approval_method of ["reject","APPROVE",null,[],1])
 expect(projectTurn({approval_method})).toEqual({});
 const effect_at="2026-10-08T12:00:00Z";
 expect(projectTurn({effect_at})).toEqual({effect_at});
 for(const value of ["invalid","",effect_at+" ".repeat(65),null,1,[]])
 expect(projectTurn({effect_at:value})).toEqual({});
 expect(projectApprovals({approvals:[{task_id:"a".repeat(32),tool_steps:[{name:"search_docs",ok:true}],approval_method:"approve",effect_at}]}))
 .toEqual({approvals:[{turn_id:"a".repeat(32)}]});
});
test("conversation list validates complete rows and limits allowed fields", () => {
 const row={conversation_id:"a".repeat(32),title:"대화",status:"completed",awaiting_approval:false,turn_count:0,updated_at:"2026-10-08T12:00:00Z"};
 const invalid=[
 null,{}, {...row,conversation_id:"A".repeat(32)}, {...row,conversation_id:"a".repeat(31)},
 {...row,title:1},{...row,title:"x".repeat(81)}, {...row,status:"unknown"},
 {...row,awaiting_approval:"false"}, {...row,turn_count:-1},{...row,turn_count:1.5},
 {...row,turn_count:"1"},{...row,updated_at:1},{...row,updated_at:"x".repeat(65)}
 ];
 const view=projectConversationList({conversations:[{...row,request:"SENTINEL",history:"SENTINEL",answer:"SENTINEL"},...invalid,{...row,title:""}]});
 expect(view).toEqual({conversations:[row,{...row,title:"새 대화"}]});
 expect(JSON.stringify(view)).not.toContain("SENTINEL");
 expect(Object.keys((view.conversations as object[])[0]).sort()).toEqual(Object.keys(row).sort());
 expect(projectConversationList({conversations:Array.from({length:51},()=>row)}).conversations).toHaveLength(50);
 for(const value of [{},null,{conversations:{}}]) expect(projectConversationList(value)).toEqual({});
});
