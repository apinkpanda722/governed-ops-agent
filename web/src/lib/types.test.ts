import { expect, test } from "vitest";
import { conversationView, conversationListView, type ConversationView } from "./types";
test("turn extensions validate and project without extra fields", () => {
 const value = conversationView({history: [], turn: {tool_steps: [{name:"search_docs",ok:true,args:"sentinel",result:"sentinel"}, {name:4,ok:true}, {name:"run_sql",ok:"yes"}, ...Array.from({length:25},()=>({name:"get_document",ok:false}))], approval_method:"edit_approve",effect_at:"2026-10-08T14:00:00Z"}} as unknown as ConversationView);
 expect(value.turn?.tool_steps).toHaveLength(20);
 expect(value.turn?.tool_steps?.[0]).toEqual({name:"search_docs",ok:true});
 expect(JSON.stringify(value)).not.toContain("sentinel");
 expect(value.turn?.approval_method).toBe("edit_approve"); expect(value.turn?.effect_at).toBe("2026-10-08T14:00:00Z");
 const invalid=conversationView({history:[],turn:{tool_steps:"bad",approval_method:"bad",effect_at:"bad"}} as unknown as ConversationView);
 expect(invalid.turn?.tool_steps).toBeUndefined();expect(invalid.turn?.approval_method).toBeUndefined();expect(invalid.turn?.effect_at).toBeUndefined();
});
test("list projects only six valid fields and caps 50",()=>{
 const row={conversation_id:"a".repeat(32),title:"제목",status:"completed",awaiting_approval:false,turn_count:1,updated_at:"2026-10-08T14:00:00Z"};
 const result=conversationListView([ {...row,request:"sentinel",answer:"sentinel"}, {...row,turn_count:"1"},null, {...row,awaiting_approval:1}, {...row,status:"bad"}]);
 expect(result).toHaveLength(1);expect(result[0]).toEqual(row);expect(Object.keys(result[0])).toHaveLength(6);
 expect(conversationListView(null)).toEqual([]);expect(conversationListView(Array(60).fill(row))).toHaveLength(50);
});
