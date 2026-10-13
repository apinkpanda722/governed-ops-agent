import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,beforeEach,expect,test,vi} from "vitest";
import {ConversationList} from "./conversation-list";
import {testids} from "@/lib/testids";
import {DELETE_CONFIRM_NOTICE} from "@/lib/messages";
const id="a".repeat(32), other="b".repeat(32);
const notice="대화를 삭제할까요? 원문은 다음 정리 때 제거되고, 감사 기록과 이미 발송된 효과는 남는다.";
const state=vi.hoisted(()=>({path:"",refresh:vi.fn(),replace:vi.fn(),bff:vi.fn()}));
vi.mock("next/navigation",()=>({usePathname:()=>state.path,useRouter:()=>({replace:state.replace})}));
vi.mock("@/lib/bff",async importOriginal=>({...await importOriginal<typeof import("@/lib/bff")>(),bff:state.bff}));
vi.mock("./conversation-list-context",()=>({useConversationList:()=>({items:["a","b"].map(letter=>({conversation_id:letter.repeat(32),title:`대화 ${letter}`,status:"completed",awaiting_approval:false,turn_count:1,updated_at:"2026-10-13T12:00:00Z"})),loading:false,error:undefined,refresh:state.refresh})}));
beforeEach(()=>{vi.clearAllMocks();state.path=`/c/${id}`;state.bff.mockResolvedValue({deleted:true});state.refresh.mockResolvedValue(undefined);});
afterEach(()=>{cleanup();});
function openConfirm() { fireEvent.click(screen.getByTestId(testids.conversationDelete(id))); }
test("real keyboard-focusable buttons show the exact inline notice and cancel without DELETE",()=>{
 render(<ConversationList/>);
 for(const key of [id,other]){
 const button=screen.getByTestId(testids.conversationDelete(key));
 expect(button.tagName).toBe("BUTTON");expect(button.tabIndex).toBeGreaterThanOrEqual(0);
 button.focus();expect(document.activeElement).toBe(button);
 }
 expect(screen.queryByText(notice)).toBeNull();expect(state.bff).not.toHaveBeenCalled();
 expect(screen.queryByTestId(testids.conversationDeleteConfirm(id))).toBeNull();
 openConfirm();expect(DELETE_CONFIRM_NOTICE).toBe(notice);expect(screen.getByText(notice).textContent).toBe(notice);
 for(const key of [testids.conversationDeleteConfirm(id),testids.conversationDeleteCancel(id)]){
 const button=screen.getByTestId(key);expect(button.tagName).toBe("BUTTON");expect(button.tabIndex).toBeGreaterThanOrEqual(0);
 button.focus();expect(document.activeElement).toBe(button);
 }
 expect(state.bff).not.toHaveBeenCalled();
 fireEvent.click(screen.getByTestId(testids.conversationDeleteCancel(id)));
 expect(screen.queryByText(notice)).toBeNull();expect(state.bff).not.toHaveBeenCalled();
 expect(state.refresh).not.toHaveBeenCalled();expect(state.replace).not.toHaveBeenCalled();
 expect(screen.getByTestId(testids.conversationItem(id))).toBeDefined();
});
test("confirmation sends exactly one DELETE, retains the item while pending and refreshes only after success",async()=>{
 let resolve!:(value:{deleted:boolean})=>void;
 state.bff.mockReturnValue(new Promise<{deleted:boolean}>(done=>{resolve=done;}));
 render(<ConversationList/>);openConfirm();
 const confirm=screen.getByTestId(testids.conversationDeleteConfirm(id));
 fireEvent.click(confirm);fireEvent.click(confirm);
 expect(state.bff).toHaveBeenCalledTimes(1);expect(state.bff).toHaveBeenCalledWith(`/api/conversations/${id}`,{method:"DELETE"});
 expect(screen.getByTestId(testids.conversationItem(id))).toBeDefined();
 expect(state.refresh).not.toHaveBeenCalled();expect(state.replace).not.toHaveBeenCalled();
 expect((confirm as HTMLButtonElement).disabled).toBe(true);
 resolve({deleted:true});
 await waitFor(()=>expect(state.refresh).toHaveBeenCalledTimes(1));
 await waitFor(()=>expect(state.replace).toHaveBeenCalledWith("/c"));
 expect(state.replace).toHaveBeenCalledTimes(1);
 expect(screen.queryByText(notice)).toBeNull();
});
test.each(["/c",`/c/${other}`,"/approvals"])("deleting from %s refreshes without leaving that page",async path=>{
 state.path=path;render(<ConversationList/>);openConfirm();fireEvent.click(screen.getByTestId(testids.conversationDeleteConfirm(id)));
 await waitFor(()=>expect(state.refresh).toHaveBeenCalledTimes(1));
 expect(state.bff).toHaveBeenCalledTimes(1);expect(state.replace).not.toHaveBeenCalled();
});
test.each([
 ["approval_pending",409,"이 대화에 처리 중인 승인 요청이 있습니다. 먼저 승인하거나 거부해 주세요."],
 ["turn_in_progress",409,"이전 요청을 처리하는 중입니다. 잠시 후 다시 시도해 주세요."],
 ["conversation_not_found",404,"대화를 찾을 수 없습니다."],
] as const)("%s displays the existing message under its item without removal",async(code,status,message)=>{
 state.bff.mockRejectedValue({code,status});render(<ConversationList/>);openConfirm();
 fireEvent.click(screen.getByTestId(testids.conversationDeleteConfirm(id)));
 await waitFor(()=>expect(screen.getByRole("alert").textContent).toBe(message));
 expect(screen.getByTestId(testids.conversationItem(id)).closest("li")?.contains(screen.getByRole("alert"))).toBe(true);
 expect(screen.getByTestId(testids.conversationItem(other)).closest("li")?.contains(screen.getByRole("alert"))).toBe(false);
 expect(state.bff).toHaveBeenCalledTimes(1);expect(state.refresh).not.toHaveBeenCalled();expect(state.replace).not.toHaveBeenCalled();
 expect(screen.getByTestId(testids.conversationItem(id))).toBeDefined();
});
test("a false deletion result keeps the item and reports unavailable upstream",async()=>{
 state.bff.mockResolvedValue({deleted:false});render(<ConversationList/>);openConfirm();
 fireEvent.click(screen.getByTestId(testids.conversationDeleteConfirm(id)));
 await waitFor(()=>expect(screen.getByRole("alert").textContent).toBe("서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."));
 expect(state.refresh).not.toHaveBeenCalled();expect(state.replace).not.toHaveBeenCalled();
 expect(screen.getByTestId(testids.conversationItem(id))).toBeDefined();
});
