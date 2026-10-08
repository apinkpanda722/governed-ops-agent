import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,beforeEach,expect,test,vi} from "vitest";
import {Sidebar} from "./sidebar";
import type {ConversationListItem} from "@/lib/types";
const state=vi.hoisted(()=>({path:"/c/"+ "a".repeat(32),items:[] as ConversationListItem[],replace:vi.fn()}));
vi.mock("next/navigation",()=>({usePathname:()=>state.path,useRouter:()=>({replace:state.replace})}));
vi.mock("./conversation-list-context",()=>({useConversationList:()=>({items:state.items,loading:false,error:undefined,refresh:vi.fn()})}));
beforeEach(()=>{
 Object.defineProperty(Element.prototype,"getAnimations",{configurable:true,value:()=>[]});
 vi.useFakeTimers({toFake:["Date"]});vi.setSystemTime(new Date(2026,9,8,14,0));
 vi.stubGlobal("ResizeObserver",class {observe(){} unobserve(){} disconnect(){}});
 vi.stubGlobal("matchMedia",()=>({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
 state.items=Array.from({length:23},(_,i)=>({conversation_id:i===0?"a".repeat(32):i.toString(16).padStart(32,"0"),title:"제목 "+i,status:"completed",awaiting_approval:i===0,turn_count:1,updated_at:new Date(2026,9,i===0?8:i===1?7:6,13,5).toISOString()}));
 vi.stubGlobal("fetch",vi.fn().mockResolvedValue(new Response('{"approvals":[{"turn_id":"a"},{"turn_id":"b"}]}')));
});
afterEach(()=>{cleanup();Reflect.deleteProperty(Element.prototype,"getAnimations");vi.useRealTimers();vi.unstubAllGlobals();vi.restoreAllMocks();vi.clearAllMocks();localStorage.clear();});
test("groups, limits, links, current item, pending count and user card",async()=>{
 render(<Sidebar user="demo-finance-g2" collapsed={false} onToggle={vi.fn()}/>);
 for(const key of ["오늘","어제","이전"])expect(screen.getByTestId("conversation-group-"+key)).toBeDefined();
 expect(screen.getAllByTestId(/^conversation-item-/)).toHaveLength(20);
 const item=screen.getByTestId("conversation-item-"+"a".repeat(32));expect(item.getAttribute("href")).toBe("/c/"+"a".repeat(32));expect(item.getAttribute("aria-current")).toBe("page");
 expect(screen.getByTestId("conversation-pending-"+"a".repeat(32)).textContent).toBe("승인 대기");
 fireEvent.click(screen.getByTestId("conversation-more"));expect(screen.getAllByTestId(/^conversation-item-/)).toHaveLength(23);
 await waitFor(()=>expect(screen.getByTestId("nav-approvals-count").textContent).toBe("2"));
 expect(screen.getByTestId("session-user").textContent).toBe("재무팀 · 2급");expect(screen.getByTestId("session-user").getAttribute("title")).toBe("demo-finance-g2");
 expect(screen.getByTestId("user-team-badge").textContent).toBe("재무팀");
});
test("logout deletes then redirects, failure displays notice",async()=>{
 const fetcher=vi.fn().mockImplementation((path:string)=>Promise.resolve(new Response(path==="/api/approvals"?'{"approvals":[]}':'{}')));
 vi.stubGlobal("fetch",fetcher);
 const view=render(<Sidebar user="demo-finance-g2" collapsed={false} onToggle={vi.fn()}/>);
 fireEvent.click(screen.getByTestId("logout"));await waitFor(()=>expect(state.replace).toHaveBeenCalledWith("/login"));
 expect(fetcher).toHaveBeenCalledWith("/api/session",expect.objectContaining({method:"DELETE"}));view.unmount();state.replace.mockClear();
 vi.stubGlobal("fetch",vi.fn().mockImplementation((path:string)=>Promise.resolve(new Response(path==="/api/approvals"?'{"approvals":[]}':'{"code":"upstream_unavailable"}',{status:path==="/api/approvals"?200:503}))));
 render(<Sidebar user="demo-finance-g2" collapsed={false} onToggle={vi.fn()}/>);
 fireEvent.click(screen.getByTestId("logout"));await waitFor(()=>expect(screen.getByTestId("conflict-notice").textContent).toContain("서버에 연결할 수 없습니다"));
 expect(state.replace).not.toHaveBeenCalled();
});
test("theme choice presses and persists only the theme",()=>{
 render(<Sidebar user="demo-finance-g2" collapsed={false} onToggle={vi.fn()}/>);
 expect(screen.getByTestId("theme-toggle-system").getAttribute("aria-pressed")).toBe("true");
 fireEvent.click(screen.getByTestId("theme-toggle-dark"));expect(screen.getByTestId("theme-toggle-dark").getAttribute("aria-pressed")).toBe("true");
 expect(localStorage.getItem("govops-theme")).toBe("dark");expect(localStorage.length).toBe(1);
});
test("empty list and zero approvals",async()=>{
 state.items=[];vi.stubGlobal("fetch",vi.fn().mockResolvedValue(new Response('{"approvals":[]}')));
 render(<Sidebar user="demo-finance-g2" collapsed={false} onToggle={vi.fn()}/>);
 expect(screen.getByText("아직 대화가 없습니다.")).toBeDefined();
 await waitFor(()=>expect(screen.queryByTestId("nav-approvals-count")).toBeNull());
});
