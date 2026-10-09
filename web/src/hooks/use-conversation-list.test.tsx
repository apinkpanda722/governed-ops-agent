import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useConversationList } from "./use-conversation-list";
import { setOnUnauthenticated } from "@/lib/bff";
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setOnUnauthenticated(); });
test.each([10000])("polls every %s, pauses hidden tabs, refreshes and cleans up", async interval => {
    vi.useFakeTimers(); let hidden = false; vi.spyOn(document, "visibilityState", "get").mockImplementation(() => hidden ? "hidden" : "visible");
    const f = vi.fn().mockImplementation(() => Promise.resolve(new Response('{"conversations":[{"conversation_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","title":"제목","status":"completed","awaiting_approval":false,"turn_count":1,"updated_at":"2026-10-08T14:00:00Z","request":"private-sentinel"}]}'))); vi.stubGlobal("fetch", f);
    const { result, unmount } = renderHook(() => useConversationList());
    await act(async () => vi.advanceTimersByTimeAsync(0)); expect(result.current.items).toHaveLength(1); expect(JSON.stringify(result.current.items)).not.toContain("private-sentinel");
    await act(async () => vi.advanceTimersByTimeAsync(interval - 1)); expect(f).toHaveBeenCalledOnce();
    await act(async () => vi.advanceTimersByTimeAsync(1)); expect(f).toHaveBeenCalledTimes(2);
    hidden = true; act(() => document.dispatchEvent(new Event("visibilitychange"))); await act(async () => vi.advanceTimersByTimeAsync(interval * 2)); expect(f).toHaveBeenCalledTimes(2);
    hidden = false; act(() => document.dispatchEvent(new Event("visibilitychange"))); await act(async () => vi.advanceTimersByTimeAsync(0)); expect(f).toHaveBeenCalledTimes(3);
    await act(async () => result.current.refresh()); expect(f).toHaveBeenCalledTimes(4);
    const refresh = result.current.refresh; unmount(); expect(vi.getTimerCount()).toBe(0); await act(async () => { await refresh(); await vi.advanceTimersByTimeAsync(interval * 2); }); expect(f).toHaveBeenCalledTimes(4);
});
test("401 uses BFF login hook and stops polling", async () => {
    vi.useFakeTimers(); const login = vi.fn(); setOnUnauthenticated(login); const f = vi.fn().mockImplementation(() => Promise.resolve(new Response('{"code":"unauthenticated"}', { status: 401 }))); vi.stubGlobal("fetch", f);
    renderHook(() => useConversationList()); await act(async () => vi.advanceTimersByTimeAsync(15000)); expect(login).toHaveBeenCalledOnce(); expect(f).toHaveBeenCalledOnce();
});
test("hidden tab resumes only one poll and aborts in-flight read on unmount", async () => {
    vi.useFakeTimers(); let hidden = false; vi.spyOn(document, "visibilityState", "get").mockImplementation(() => hidden ? "hidden" : "visible");
    let resolve!: (r: Response) => void; let signal: AbortSignal | undefined;
    const f = vi.fn((_path: string, options: RequestInit) => { signal = options.signal as AbortSignal; return new Promise<Response>(r => { resolve = r; }); }); vi.stubGlobal("fetch", f);
    const { unmount } = renderHook(() => useConversationList());
    hidden = true; act(() => document.dispatchEvent(new Event("visibilitychange"))); hidden = false; act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(f).toHaveBeenCalledOnce(); unmount(); expect(signal?.aborted).toBe(true);
    await act(async () => { resolve(new Response('{"conversations":[]}')); await vi.advanceTimersByTimeAsync(15000); }); expect(f).toHaveBeenCalledOnce(); expect(vi.getTimerCount()).toBe(0);
});
