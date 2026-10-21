// @vitest-environment node
import { randomBytes } from "node:crypto";
import { createServer, type Server } from "node:http";
import { once } from "node:events";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { NextRequest } from "next/server";
import * as session from "./session/route";
import * as demo from "./demo-users/route";
import * as conversations from "./conversations/route";
import * as conversation from "./conversations/[id]/route";
import * as turns from "./conversations/[id]/turns/route";
import * as events from "./conversations/[id]/events/route";
import * as approvals from "./approvals/route";
import * as approval from "./tasks/[id]/approval/route";
import * as audit from "./conversations/[id]/audit/route";
import * as verify from "./audit/verify/route";

let server:Server;
let token:string;
let valid:boolean;
let calls:number;
let receivedAuth:string|undefined;
let receivedBody:unknown;
let receivedUrl:string|undefined;
let receivedMethod:string|undefined;
let failure:{status:number;detail:string}|undefined;
let closed:Promise<unknown>;
let timer:ReturnType<typeof setInterval>|undefined;
const id="a".repeat(32);
const context={params:Promise.resolve({id})};
const safe={task_id:id,turn_id:id,status:"awaiting_approval",answer:"safe answer",cited_doc_ids:["syn-doc"],draft:{tool:"jira_add_comment",args:{ticket_id:"syn-ticket",body:"draft"}},check:{status:"passed",message:"checked"},expires_at:"2026-10-08T12:00:00Z",draft_sha256:"b".repeat(64)};
function req(path:string,method="GET",body?:unknown, options:{cookie?:string;origin?:string|null;host?:string;signal?:AbortSignal}={}) {
 const headers=new Headers({host:options.host??"127.0.0.1:3100"});
 if(options.origin!==null) headers.set("origin",options.origin??"http://127.0.0.1:3100");
 if(options.cookie!==undefined) headers.set("cookie",`govops_session=${options.cookie}`);
 return new NextRequest(`http://localhost:3100/api/${path}`,{method,headers,body:body===undefined?undefined:JSON.stringify(body),signal:options.signal});
}
beforeEach(async()=>{
 token=randomBytes(32).toString("base64url"); valid=true; calls=0; failure=undefined;
 server=createServer(async (request,response)=>{
  calls++; receivedAuth=request.headers.authorization; receivedUrl=request.url; receivedMethod=request.method;
  const chunks=[]; for await(const chunk of request) chunks.push(chunk);
  receivedBody=chunks.length?JSON.parse(Buffer.concat(chunks).toString()):undefined;
  response.setHeader("content-type","application/json");
  if(failure){ response.writeHead(failure.status);response.end(JSON.stringify({detail:failure.detail,secret:token}));return; }
  if(request.url==="/demo/users"){response.end(JSON.stringify({users:["demo-platform-g3"],secret:token}));return;}
  if(request.url==="/sessions"&&request.method==="POST"){response.writeHead(201);response.end(JSON.stringify({session_token:token,expires_at:Date.now()/1000+28800}));return;}
  if(!valid||receivedAuth!==`Bearer ${token}`){response.writeHead(401);response.end(JSON.stringify({detail:"Unauthorized",secret:token}));return;}
  if(request.url==="/sessions"&&request.method==="DELETE"){valid=false;response.writeHead(204);response.end();return;}
  if(request.url==="/sessions/me"){response.end(JSON.stringify({user_id:"demo-platform-g3",secret:token}));return;}
  if(request.url?.endsWith("/events")){
   response.setHeader("content-type","text/event-stream");
   closed=once(response,"close");
   response.write(`data: ${JSON.stringify({conversation_id:id,turn_id:id,status:"running",seq:1})}\n\n`);
   timer=setInterval(()=>response.write(`data: ${JSON.stringify({conversation_id:id,turn_id:id,status:"completed",seq:2})}\n\n`),100);
   response.on("close",()=>clearInterval(timer));
   return;
  }
  const junk={rules:"SENTINEL-rules",messages:token,guardrail:{masked:token},reasoning:"SENTINEL-reason"};
  if(request.method==="DELETE"&&request.url===`/conversations/${id}`){
   response.end(JSON.stringify({conversation_id:id,deleted:true,secret:token,...junk}));return;
  }
  if(request.method==="GET"&&request.url?.startsWith("/conversations?")){
   response.end(JSON.stringify({conversations:[{conversation_id:id,title:"대화",status:"completed",awaiting_approval:false,turn_count:1,updated_at:"2026-10-08T12:00:00Z",request:"SENTINEL",history:"SENTINEL",answer:"SENTINEL",secret:token}],secret:token}));return;
  }
  if(request.url==="/approvals"){response.end(JSON.stringify({approvals:[{...safe,...junk,requester_label:"demo-platform-g3",history:"SENTINEL-history",conversation_id:id}]}));return;}
  if(request.method==="POST")response.statusCode=202;
  response.end(JSON.stringify(request.method==="GET"?{conversation_id:id,turn_index:1,history:[{role:"assistant",content:"previous"}],turn:{...safe,...junk}}:{conversation_id:id,...safe,...junk}));
 });
 server.listen(0,"127.0.0.1");await once(server,"listening",{signal:AbortSignal.timeout(2000)});
 const address=server.address();if(!address||typeof address==="string")throw new Error("stub address");
 vi.stubEnv("GOVOPS_API_URL",`http://127.0.0.1:${address.port}`);
});
afterEach(async()=>{
 clearInterval(timer);server.closeAllConnections();
 await new Promise<void>((resolve,reject)=>{const limit=setTimeout(()=>reject(new Error("close timeout")),2000);server.close(()=>{clearTimeout(limit);resolve();});});
 vi.unstubAllEnvs();
});
test("login, session, public labels and single-session logout",async()=>{
 const login=await session.POST(req("session","POST",{user:"demo-platform-g3"}));
 expect(login.status).toBe(201);
 const cookie=login.headers.get("set-cookie")!;
 for(const attr of ["HttpOnly","SameSite=strict","Path=/","Max-Age="])expect(cookie).toContain(attr);
 const body=await login.json();expect(body).toEqual({ok:true,user_id:"demo-platform-g3"});
 expect(JSON.stringify(body).includes(token)).toBe(false);
 expect(await (await demo.GET(req("demo-users"))).json()).toEqual({users:["demo-platform-g3"]});
 expect(await (await session.GET(req("session","GET",undefined,{cookie:token}))).json()).toEqual({user_id:"demo-platform-g3"});
 for(const cookie of [undefined,"bad.value",randomBytes(16).toString("base64url")]){
 const response=await session.GET(req("session","GET",undefined,{cookie}));expect(response.status).toBe(401);
 expect(response.headers.get("set-cookie")).toContain("Expires=Thu, 01 Jan 1970");
 }
 const logout=await session.DELETE(req("session","DELETE",undefined,{cookie:token}));
 expect(logout.status).toBe(200);expect(valid).toBe(false);
 expect(logout.headers.get("set-cookie")).toContain("Expires=Thu, 01 Jan 1970");
 expect((await session.GET(req("session","GET",undefined,{cookie:token}))).status).toBe(401);
 expect((await session.DELETE(req("session","DELETE",undefined,{cookie:token}))).status).toBe(200);
});
test("mutation origin, missing authentication, IDs and bodies reject before upstream",async()=>{
 const mutations=[
 ()=>session.POST(req("session","POST",{user:"demo-platform-g3"},{origin:null})),
 ()=>session.DELETE(req("session","DELETE",undefined,{origin:"http://evil.test",cookie:token})),
 ()=>conversations.POST(req("conversations","POST",{request:"hello"},{origin:null,cookie:token})),
 ()=>turns.POST(req("turns","POST",{request:"hello"},{origin:"http://evil.test",cookie:token}),context),
 ()=>approval.POST(req("approval","POST",{decision:"approve",draft_sha256:"b".repeat(64)},{origin:null,cookie:token}),context),
 ];
 for(const mutate of mutations)expect((await mutate()).status).toBe(403);
 expect(calls).toBe(0);
 expect((await conversations.POST(req("conversations","POST",{request:"hello"}))).status).toBe(401);
 for(const body of [{request:"",user:"demo-platform-g3"},{request:"x",team:"platform"},{request:"x".repeat(2001)},{}]){
 expect((await conversations.POST(req("conversations","POST",body,{cookie:token}))).status).toBe(422);
 expect((await turns.POST(req("turns","POST",body,{cookie:token}),context)).status).toBe(422);
 }
 for(const body of [{decision:"approve",draft_sha256:"b".repeat(64),edited:{body:"x"}},{decision:"edit_approve",draft_sha256:"b".repeat(64),edited:{body:5}},{decision:"approve",draft_sha256:"bad"},{decision:"approve",draft_sha256:"b".repeat(64),owner:"x"}])
 expect((await approval.POST(req("approval","POST",body,{cookie:token}),context)).status).toBe(422);
 expect((await conversation.GET(req("conversation","GET",undefined,{cookie:token}),{params:Promise.resolve({id:"bad"})})).status).toBe(404);
 expect(calls).toBe(0);
});
test("valid operations, projection and safe error mapping",async()=>{
 const edit={decision:"edit_approve",draft_sha256:"b".repeat(64),edited:{body:"edited"}};
 const operations:[()=>Promise<Response>,unknown][]=[
 [()=>conversations.POST(req("conversations","POST",{request:"hello"},{cookie:token})),{request:"hello"}],
 [()=>turns.POST(req("turns","POST",{request:"hello"},{cookie:token}),context),{request:"hello"}],
 [()=>approval.POST(req("approval","POST",edit,{cookie:token}),context),edit],
 ];
 for(const [execute,expected] of operations){
 const response=await execute();expect(response.status).toBe(202);
 expect(receivedAuth===`Bearer ${token}`).toBe(true);expect(receivedBody).toEqual(expected);
 }
 for(const execute of [()=>conversation.GET(req("conversation","GET",undefined,{cookie:token}),context),()=>approvals.GET(req("approvals","GET",undefined,{cookie:token}))]){
 const response=await execute();expect(response.headers.get("cache-control")).toBe("no-store");
 const text=await response.text();expect(text).not.toContain("SENTINEL");expect(text.includes(token)).toBe(false);expect(text).toContain("draft");expect(text).toContain("checked");
 }
 for(const detail of ["approval_pending","turn_in_progress","turn_limit","conversation_not_found","invalid_request"]){
 failure={status:detail==="conversation_not_found"?404:409,detail};
 const response=await conversations.POST(req("conversations","POST",{request:"hello"},{cookie:token}));
 expect(response.status).toBe(failure.status);expect(await response.json()).toEqual({code:detail});
 }
 for(const [status,code] of [[403,"approval_forbidden"],[404,"task_not_found"],[409,"approval_conflict"],[422,"invalid_edit"],[500,"upstream_unavailable"]] as const){
 failure={status,detail:"SENTINEL-error"};
 const response=await approval.POST(req("approval","POST",{decision:"approve",draft_sha256:"b".repeat(64)},{cookie:token}),context);
 expect(response.status).toBe(status===500?502:status);expect(await response.json()).toEqual({code});
 }
});
test("login mappings and logout clears even on upstream failure",async()=>{
 for(const [status,detail,code] of [[403,"anything","loopback_required"],[404,"User not found","user_not_found"],[404,"Not Found","demo_login_disabled"]] as const){
 failure={status,detail};expect(await (await session.POST(req("session","POST",{user:"demo-platform-g3"}))).json()).toEqual({code});
 }
 failure={status:500,detail:"private"};
 const response=await session.DELETE(req("session","DELETE",undefined,{cookie:token}));
 expect(response.status).toBe(502);expect(response.headers.get("set-cookie")).toContain("Expires=Thu, 01 Jan 1970");
});
test("SSE chunks stream immediately and cancellation closes upstream socket",async()=>{
 const controller=new AbortController();
 const response=await events.GET(req("events","GET",undefined,{cookie:token,signal:controller.signal}),context);
 expect(response.headers.get("content-type")).toBe("text/event-stream");
 expect(response.headers.get("cache-control")).toBe("no-cache, no-transform");
 expect(response.headers.get("x-accel-buffering")).toBe("no");
 const reader=response.body!.getReader();
 const first=await reader.read();expect(new TextDecoder().decode(first.value)).toContain('"seq":1');
 const second=await reader.read();expect(new TextDecoder().decode(second.value)).toContain('"seq":2');
 controller.abort();
 let timeout:ReturnType<typeof setTimeout>|undefined;
 try {await Promise.race([closed,new Promise((_,reject)=>{timeout=setTimeout(()=>reject(new Error("abort not propagated")),1500);})]);}
 finally{clearTimeout(timeout);await reader.cancel().catch(()=>{});}
});
test("SSE rejects unauthenticated, invalid ID, errors and non-stream responses",async()=>{
 expect((await events.GET(req("events"),context)).status).toBe(401);
 expect(calls).toBe(0);
 expect((await events.GET(req("events","GET",undefined,{cookie:token}),{params:Promise.resolve({id:"bad"})})).status).toBe(404);
 expect(calls).toBe(0);
 for(const status of [401,404,403,500]){
 failure={status,detail:"SENTINEL-private"};
 const result=await events.GET(req("events","GET",undefined,{cookie:token}),context);
 expect(result.status).toBe(status===401||status===404?status:502);
 const text=await result.text();expect(text).not.toContain("SENTINEL");expect(text.includes(token)).toBe(false);
 if(status===401)expect(result.headers.get("set-cookie")).toContain("Expires=Thu, 01 Jan 1970");
 }
 failure={status:200,detail:"SENTINEL-private"};
 expect((await events.GET(req("events","GET",undefined,{cookie:token}),context)).status).toBe(502);
});
test("malformed JSON, login format and all dynamic IDs are rejected locally",async()=>{
 const malformed=req("session","POST"); Object.defineProperty(malformed,"json",{value:()=>Promise.reject(new SyntaxError("synthetic"))});
 expect((await session.POST(malformed)).status).toBe(422);
 for(const user of ["other","demo-Platform-g3","demo-platform-gx"]){
 expect((await session.POST(req("session","POST",{user}))).status).toBe(422);
 }
 expect((await session.POST(req("session","POST",{user:"demo-platform-g3",team:"platform"}))).status).toBe(422);
 const invalid={params:Promise.resolve({id:"z".repeat(32)})};
 expect((await turns.POST(req("turns","POST",{request:"hello"},{cookie:token}),invalid)).status).toBe(404);
 expect((await approval.POST(req("approval","POST",{decision:"approve",draft_sha256:"b".repeat(64)},{cookie:token}),invalid)).status).toBe(404);
 expect(calls).toBe(0);
});
test("connection failure becomes a fixed 502 and logout still clears cookie",async()=>{
 await new Promise<void>(resolve=>server.close(()=>resolve()));
 const response=await conversation.GET(req("conversation","GET",undefined,{cookie:token}),context);
 expect(response.status).toBe(502);expect(await response.json()).toEqual({code:"upstream_unavailable"});
 const logout=await session.DELETE(req("session","DELETE",undefined,{cookie:token}));
 expect(logout.status).toBe(502);expect(logout.headers.get("set-cookie")).toContain("Expires=Thu, 01 Jan 1970");
});

test("conversation list authenticates, projects and fixes the upstream query",async()=>{
 const missing=await conversations.GET(req("conversations"));
 expect(missing.status).toBe(401);expect(calls).toBe(0);
 expect(missing.headers.get("cache-control")).toBe("no-store");
 const response=await conversations.GET(req("conversations?limit=1000&owner=other","GET",undefined,{cookie:token,origin:"http://evil.test"}));
 expect(response.status).toBe(200);expect(calls).toBe(1);
 expect(receivedUrl).toBe("/conversations?limit=50");
 expect(receivedAuth===`Bearer ${token}`).toBe(true);
 expect(response.headers.get("cache-control")).toBe("no-store");
 const body=await response.json();
 expect(body).toEqual({conversations:[{conversation_id:id,title:"대화",status:"completed",awaiting_approval:false,turn_count:1,updated_at:"2026-10-08T12:00:00Z"}]});
 expect(JSON.stringify(body)).not.toContain("SENTINEL");expect(JSON.stringify(body).includes(token)).toBe(false);
});
test("conversation list clears unauthorized sessions and maps upstream failures",async()=>{
 for(const status of [401,500,503]){
 failure={status,detail:"SENTINEL"};
 const response=await conversations.GET(req("conversations","GET",undefined,{cookie:token,origin:null}));
 expect(response.status).toBe(status===401?401:502);
 expect(response.headers.get("cache-control")).toBe("no-store");
 expect(await response.json()).toEqual({code:status===401?"unauthenticated":"upstream_unavailable"});
 if(status===401)expect(response.headers.get("set-cookie")).toContain("Expires=Thu, 01 Jan 1970");
 }
 server.closeAllConnections();
 await new Promise<void>((resolve,reject)=>{const limit=setTimeout(()=>reject(new Error("close timeout")),2000);server.close(()=>{clearTimeout(limit);resolve();});});
 const response=await conversations.GET(req("conversations","GET",undefined,{cookie:token}));
 expect(response.status).toBe(502);expect(await response.json()).toEqual({code:"upstream_unavailable"});
 expect(response.headers.get("cache-control")).toBe("no-store");
});

test("conversation DELETE rejects missing cookies, origin and malformed IDs before upstream",async()=>{
 const missing=await conversation.DELETE(req(`conversations/${id}`,"DELETE"),context);
 expect(missing.status).toBe(401);expect(await missing.json()).toEqual({code:"unauthenticated"});
 expect(calls).toBe(0);
 for(const origin of [null,"http://evil.test"]){
 const response=await conversation.DELETE(req(`conversations/${id}`,"DELETE",undefined,{cookie:token,origin}),context);
 expect(response.status).toBe(403);expect(await response.json()).toEqual({code:"origin_mismatch"});
 expect(calls).toBe(0);
 }
 for(const bad of ["bad","A".repeat(32),"a".repeat(31),"z".repeat(32)]){
 const response=await conversation.DELETE(req(`conversations/${bad}`,"DELETE",undefined,{cookie:token}),{params:Promise.resolve({id:bad})});
 expect(response.status).toBe(404);expect(await response.json()).toEqual({code:"not_found"});
 expect(calls).toBe(0);
 }
});
test("conversation DELETE forwards its method and projects only deleted",async()=>{
 const response=await conversation.DELETE(req(`conversations/${id}`,"DELETE",undefined,{cookie:token}),context);
 expect(response.status).toBe(200);expect(response.headers.get("cache-control")).toBe("no-store");
 expect(await response.json()).toEqual({deleted:true});
 expect(calls).toBe(1);expect(receivedUrl).toBe(`/conversations/${id}`);expect(receivedMethod).toBe("DELETE");
 expect(receivedAuth===`Bearer ${token}`).toBe(true);expect(receivedBody).toBeUndefined();
});
test.each([[404,"conversation_not_found"],[409,"approval_pending"],[409,"turn_in_progress"],[500,"upstream_unavailable"],[503,"upstream_unavailable"]] as const)("conversation DELETE maps %s %s safely",async(status,code)=>{
 failure={status,detail:status>=500?"SENTINEL-error":code};
 const response=await conversation.DELETE(req(`conversations/${id}`,"DELETE",undefined,{cookie:token}),context);
 expect(response.status).toBe(status>=500?502:status);expect(await response.json()).toEqual({code});
 expect(calls).toBe(1);
});

const recordRoutes=[
 {path:`conversations/${id}/audit`,get:(request:NextRequest)=>audit.GET(request,context)},
 {path:"audit/verify",get:(request:NextRequest)=>verify.GET(request)},
];
test("B1 records require a cookie without calling upstream",async()=>{
 for(const route of recordRoutes){
 const response=await route.get(req(route.path));expect(response.status).toBe(401);
 expect(await response.json()).toEqual({code:"unauthenticated"});
 } expect(calls).toBe(0);
});
test("B2 records reject a mismatched Origin without calling upstream",async()=>{
 for(const route of recordRoutes){
 const response=await route.get(req(route.path,"GET",undefined,{cookie:token,origin:"http://evil.test"}));
 expect(response.status).toBe(403);expect(await response.json()).toEqual({code:"origin_mismatch"});
 } expect(calls).toBe(0);
});
test("B3 malformed record IDs fail locally",async()=>{
 for(const bad of ["bad","A".repeat(32),"a".repeat(31),"../audit/verify"]){
 const response=await audit.GET(req("records","GET",undefined,{cookie:token}),{params:Promise.resolve({id:bad})});
 expect(response.status).toBe(404);expect(await response.json()).toEqual({code:"not_found"});
 } expect(calls).toBe(0);
});
test("B4 upstream record 404 projects only conversation_not_found",async()=>{
 failure={status:404,detail:"conversation_not_found"};
 const response=await audit.GET(req(recordRoutes[0].path,"GET",undefined,{cookie:token}),context);
 expect(response.status).toBe(404);expect(await response.json()).toEqual({code:"conversation_not_found"});
 expect(calls).toBe(1);
});
test.each([500,503])("B5 record upstream %s becomes a fixed 502",async status=>{
 failure={status,detail:"SENTINEL-error"};
 for(const route of recordRoutes){
 const response=await route.get(req(route.path,"GET",undefined,{cookie:token}));
 expect(response.status).toBe(502);expect(await response.json()).toEqual({code:"upstream_unavailable"});
 } expect(calls).toBe(2);
});
test("B6 B7 records strip nested secrets and expose no session value in body or headers",async()=>{
 const ts="2026-10-16T01:02:03+00:00";
 const junk={kid:"SENTINEL",sig:"f".repeat(128),hash:"e".repeat(64),path:"/private/audit",secret:token};
 const event={seq:1,ts,event:"approval.approved",actor:"demo-platform-g4",hash12:"b".repeat(12)};
 const expected=[{conversation_id:id,turns:[{turn_id:id,turn_index:1,events:[event]}],truncated:false},
 {status:"pass",checked_at:ts,chain:{records:1,files:1,anchors_checked:0,problems:[{code:"seq_gap",seq:1}]},anchors:{committed:0,pending:0},legacy_unverified:{files:0,lines:0},known_limit:"tail_after_last_anchor_not_detected"}];
 server.removeAllListeners("request");
 server.on("request",(request,response)=>{
 calls++;receivedUrl=request.url;receivedMethod=request.method;receivedAuth=request.headers.authorization;
 const body=request.url==="/audit/verify"?{...expected[1],...junk,chain:{records:1,files:1,anchors_checked:0,problems:[{code:"seq_gap",seq:1,...junk}],...junk},anchors:{committed:0,pending:0,...junk},legacy_unverified:{files:0,lines:0,...junk}}:
 {...expected[0],...junk,turns:[{turn_id:id,turn_index:1,...junk,events:[{...event,...junk},{seq:2,event:"e".repeat(64),actor:"/private/audit",cost_usd:token}]}]};
 response.setHeader("content-type","application/json");response.setHeader("x-private",token);response.end(JSON.stringify(body));
 });
 for(const [i,route] of recordRoutes.entries()){
 const response=await route.get(req(route.path,"GET",undefined,{cookie:token}));
 expect(response.status).toBe(200);expect(receivedUrl).toBe(`/`+route.path);expect(receivedMethod).toBe("GET");expect(receivedAuth===`Bearer ${token}`).toBe(true);
 const body=await response.json();
 const target=i===0?{...expected[0],turns:[{turn_id:id,turn_index:1,events:[event,{seq:2}]}]}:expected[1];
 expect(body).toEqual(target);
 const serialized=JSON.stringify(body);expect(serialized).not.toMatch(/SENTINEL|[0-9a-f]{64}|kid|sig|private/);
 expect(serialized.includes(token)).toBe(false);
 expect(JSON.stringify([...response.headers]).includes(token)).toBe(false);expect(response.headers.get("x-private")).toBeNull();
 expect(response.headers.get("cache-control")).toBe("no-store");
 } expect(calls).toBe(2);
});
test("tool.call BFF response retains display fields and drops upstream argument hashes",async()=>{
 const event={seq:1,event:"tool.call",tool_name:"search_docs",status:"ok",result_count:2};
 const hidden={args_hmac:"d".repeat(64),args_hmac_kid:"synthetic-hidden-kid",tool_call_sha256:"f".repeat(64)};
 server.removeAllListeners("request");
 server.on("request",(request,response)=>{
 calls++;receivedUrl=request.url;
 response.setHeader("content-type","application/json");
 response.end(JSON.stringify({conversation_id:id,turns:[{turn_id:id,turn_index:1,events:[{...event,...hidden}]}],truncated:false}));
 });
 const response=await audit.GET(req(`conversations/${id}/audit`,"GET",undefined,{cookie:token}),context);
 expect(response.status).toBe(200);expect(calls).toBe(1);expect(receivedUrl).toBe(`/conversations/${id}/audit`);
 const body=await response.json();
 expect(body).toEqual({conversation_id:id,turns:[{turn_id:id,turn_index:1,events:[event]}],truncated:false});
 for(const [key,value] of Object.entries(hidden)) {expect(JSON.stringify(body)).not.toContain(key);expect(JSON.stringify(body)).not.toContain(value);}
});
