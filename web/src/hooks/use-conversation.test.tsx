import { StrictMode } from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useConversation } from "./use-conversation";
import { setOnUnauthenticated } from "@/lib/bff";
class Stream {
    closed = false;
    onerror: (() => void) | null = null;
    listener = () => { };
    close = vi.fn(() => { this.closed = true; });
    addEventListener(_name: string, fn: () => void) { this.listener = fn; }
}
const streams: Stream[] = [];
const deps = { eventSource: (path: string) => { expect(path).toBe("/api/conversations/id/events"); const s = new Stream(); streams.push(s); return s; } };
function view(status: string) { return new Response(JSON.stringify({ conversation_id: "id", history: [], turn: { turn_id: "t", status } })); }
afterEach(() => { cleanup(); streams.length = 0; vi.useRealTimers(); vi.unstubAllGlobals(); setOnUnauthenticated(); });
test("terminal status closes stream", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(view("running")).mockResolvedValueOnce(view("completed")));
    renderHook(() => useConversation("id", deps));
    await waitFor(() => expect(streams).toHaveLength(1), { timeout: 1000 });
    act(() => streams[0].listener());
    await waitFor(() => expect(streams[0].close).toHaveBeenCalledOnce(), { timeout: 1000 });
});
test("stream error closes and polls every two seconds until terminal", async () => {
    vi.useFakeTimers();
    const f = vi.fn().mockResolvedValueOnce(view("awaiting_approval")).mockResolvedValueOnce(view("running")).mockResolvedValueOnce(view("completed"));
    vi.stubGlobal("fetch", f);
    renderHook(() => useConversation("id", deps));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(streams).toHaveLength(1);
    act(() => streams[0].onerror?.());
    expect(streams[0].close).toHaveBeenCalledOnce();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(f).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(f).toHaveBeenCalledTimes(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f).toHaveBeenCalledTimes(3);
    expect(vi.getTimerCount()).toBe(0);
});
test("StrictMode, id change and unmount balance streams and timers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(view("running"))));
    const factory = () => { const s = new Stream(); streams.push(s); return s; };
    const { rerender, unmount } = renderHook(({ id }) => useConversation(id, { eventSource: factory }), { initialProps: { id: "id" }, wrapper: StrictMode });
    await waitFor(() => expect(streams.length).toBeGreaterThan(0), { timeout: 1000 });
    rerender({ id: "next" });
    await waitFor(() => expect(streams.length).toBeGreaterThan(1), { timeout: 1000 });
    unmount();
    expect(streams.filter(s => !s.closed)).toHaveLength(0);
});
test("401 invokes the unauthenticated hook", async () => {
    const fn = vi.fn();
    setOnUnauthenticated(fn);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"code":"unauthenticated"}', { status: 401 })));
    renderHook(() => useConversation("id", deps));
    await waitFor(() => expect(fn).toHaveBeenCalledOnce(), { timeout: 1000 });
    expect(streams).toHaveLength(0);
});
test("polling timers are cleaned up on unmount", async () => {
    vi.useFakeTimers();
    const f = vi.fn().mockImplementation(() => Promise.resolve(view("running")));
    vi.stubGlobal("fetch", f);
    const { unmount } = renderHook(() => useConversation("id", deps));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    act(() => streams[0].onerror?.());
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
    expect(streams.filter(s => !s.closed)).toHaveLength(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f).toHaveBeenCalledTimes(1);
});
test("blocked request is discarded before state, and cards deduplicate", async () => {
    const payload = { conversation_id: "id", history: [], turn: { turn_id: "blocked", status: "blocked_input", request: "private-sentinel", block: { code: "pii_input", message: "안내", extra: "private-sentinel" } } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(view("running")).mockImplementation(() => Promise.resolve(new Response(JSON.stringify(payload)))));
    const { result } = renderHook(() => useConversation("id", deps));
    await waitFor(() => expect(streams).toHaveLength(1), { timeout: 1000 });
    act(() => streams[0].listener());
    await waitFor(() => expect(result.current.blockedCards).toHaveLength(1), { timeout: 1000 });
    expect(result.current.view?.turn?.request).toBeUndefined();
    expect(JSON.stringify(result.current.view).includes("private-sentinel")).toBe(false);
    expect(result.current.blockedCards).toEqual([{ turn_id: "blocked", block: { code: "pii_input", message: "안내" }, afterMessages: 0 }]);
    await act(async () => { await result.current.submitTurn("normal request"); });
    expect(result.current.blockedCards).toHaveLength(1);
});
test("first turn creates a conversation and subsequent failures return only code", async () => {
    const f = vi.fn().mockResolvedValueOnce(new Response('{"conversation_id":"created"}', { status: 202 })).mockResolvedValueOnce(new Response('{"code":"turn_limit","detail":"private-sentinel"}', { status: 409 }));
    vi.stubGlobal("fetch", f);
    const { result } = renderHook(() => useConversation());
    await act(async () => { expect(await result.current.submitTurn("normal request")).toEqual({ conversation_id: "created" }); });
    expect(f).toHaveBeenCalledWith("/api/conversations", expect.objectContaining({ method: "POST", body: '{"request":"normal request"}' }));
    await act(async () => { expect(await result.current.submitTurn("normal request")).toEqual({ code: "turn_limit" }); });
});
