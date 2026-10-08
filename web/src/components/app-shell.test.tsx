import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,beforeEach,expect,test,vi} from "vitest";
import {AppShell} from "./app-shell";
const state=vi.hoisted(()=>({path:"/c/"+"a".repeat(32),mobile:false,listeners:new Set<()=>void>()}));
vi.mock("next/navigation",()=>({usePathname:()=>state.path,useRouter:()=>({replace:vi.fn()})}));
vi.mock("./conversation-list-context",()=>({useConversationList:()=>({items:[{conversation_id:"a".repeat(32),title:"운영 문의",status:"completed",awaiting_approval:false,turn_count:1,updated_at:"2026-10-08T14:00:00Z"}],loading:false})}));
beforeEach(()=>{
 Object.defineProperty(Element.prototype,"getAnimations",{configurable:true,value:()=>[]});
 state.mobile=false;
 vi.stubGlobal("ResizeObserver",class{observe(){} unobserve(){} disconnect(){}});
 vi.stubGlobal("matchMedia",(query:string)=>({matches:query.includes("max-width")&&state.mobile,addEventListener:vi.fn((_e:string,fn:()=>void)=>state.listeners.add(fn)),removeEventListener:vi.fn((_e:string,fn:()=>void)=>state.listeners.delete(fn))}));
 vi.stubGlobal("fetch",vi.fn().mockResolvedValue(new Response('{"approvals":[]}')));
});
afterEach(()=>{cleanup();Reflect.deleteProperty(Element.prototype,"getAnimations");state.listeners.clear();vi.unstubAllGlobals();vi.restoreAllMocks();localStorage.clear();});
test.each([["/c/"+"a".repeat(32),"운영 문의"],["/c/missing","새 대화"],["/c","새 대화"],["/approvals","승인함"]])("topbar title for %s",(path,title)=>{
 state.path=path;render(<AppShell user="demo-finance-g2">본문</AppShell>);
 expect(screen.getByTestId("topbar-title").textContent).toBe(title);
 expect(screen.getByTestId("agent-description").textContent).toBe("내 권한으로 문서·티켓을 조회하고, 승인을 받은 뒤에만 발송합니다.");
 expect(screen.getByRole("main").textContent).toBe("본문");
});
test("collapse is memory only and resets on remount",()=>{
 const view=render(<AppShell user="demo-finance-g2">본문</AppShell>);
 fireEvent.click(screen.getByTestId("sidebar-toggle"));expect(screen.getByTestId("sidebar").getAttribute("data-collapsed")).toBe("true");expect(localStorage.length).toBe(0);
 view.unmount();render(<AppShell user="demo-finance-g2">본문</AppShell>);expect(screen.getByTestId("sidebar").getAttribute("data-collapsed")).toBe("false");
});
test("mobile drawer contents mount only on open and have unique ids",async()=>{
 state.mobile=true;render(<AppShell user="demo-finance-g2">본문</AppShell>);
 expect(screen.queryByTestId("sidebar")).toBeNull();expect(screen.queryByTestId("sidebar-new-chat")).toBeNull();
 fireEvent.click(screen.getByTestId("sidebar-open"));
 await waitFor(()=>expect(screen.getAllByTestId("sidebar")).toHaveLength(1));
 expect(screen.getAllByTestId("sidebar-new-chat")).toHaveLength(1);
 fireEvent.click(screen.getByRole("button",{name:"Close"}));
 await waitFor(()=>expect(screen.queryByTestId("sidebar")).toBeNull());
});
