"use client";
import { useEffect, useRef, useState } from "react";
import { apiError, bff } from "@/lib/bff";
import { conversationView, type ApiError, type BlockedCardView, type ConversationView, type SubmitResult } from "@/lib/types";
type Stream = {
    close: () => void;
    onerror: ((event: Event) => void) | null;
    addEventListener: (name: string, listener: () => void) => void;
};
export type ConversationDeps = {
    eventSource?: (path: string) => Stream;
    setTimeout?: typeof globalThis.setTimeout;
    clearTimeout?: typeof globalThis.clearTimeout;
};
const createStream = (path: string): Stream => new EventSource(path);
const isActive = (v?: ConversationView) => ["running", "awaiting_approval"].includes(v?.turn?.status ?? "");
type Snapshot = {
    id?: string;
    view?: ConversationView;
    blockedCards: BlockedCardView[];
    error?: ApiError;
};
export function useConversation(id?: string, deps: ConversationDeps = {}) {
    const [snapshot, setSnapshot] = useState<Snapshot>({ blockedCards: [] });
    const refresh = useRef<(() => Promise<void>) | null>(null);
    const eventSource = deps.eventSource ?? createStream, schedule = deps.setTimeout ?? globalThis.setTimeout, cancel = deps.clearTimeout ?? globalThis.clearTimeout;
    useEffect(() => {
        let alive = true, stream: Stream | undefined, timer: ReturnType<typeof setTimeout> | undefined, polling = false, reading = false, again = false;
        let current: ConversationView | undefined;
        const cards: BlockedCardView[] = [];
        const controller = new AbortController();
        const close = () => { stream?.close(); stream = undefined; };
        const stopTimer = () => { if (timer !== undefined) {
            cancel(timer);
            timer = undefined;
        } };
        const poll = () => { if (alive && polling && isActive(current) && timer === undefined)
            timer = schedule(() => { timer = undefined; void read(); }, 2000); };
        async function read() {
            if (!alive || !id)
                return;
            if (reading) {
                again = true;
                return;
            }
            reading = true;
            try {
                const value = conversationView(await bff<ConversationView>(`/api/conversations/${id}`, { signal: controller.signal }));
                if (!alive)
                    return;
                current = value;
                const t = value.turn;
                if (t?.status === "blocked_input" && t.turn_id && !cards.some(c => c.turn_id === t.turn_id))
                    cards.push({ turn_id: t.turn_id, block: { code: t.block?.code, message: t.block?.message }, afterMessages: value.history.length });
                setSnapshot({ id, view: value, blockedCards: [...cards] });
                if (!isActive(value)) {
                    close();
                    stopTimer();
                    polling = false;
                }
                else if (!stream && !polling) {
                    try {
                        stream = eventSource(`/api/conversations/${id}/events`);
                        stream.addEventListener("status", () => { void read(); });
                        stream.onerror = () => { if (!alive)
                            return; close(); polling = true; poll(); };
                    }
                    catch {
                        polling = true;
                    }
                }
            }
            catch (e) {
                if (alive) {
                    const error = apiError(e);
                    setSnapshot(s => ({ ...s, id, error }));
                    if (error.status === 401) {
                        close();
                        stopTimer();
                        polling = false;
                    }
                }
            }
            finally {
                reading = false;
                if (alive) {
                    if (again) {
                        again = false;
                        void read();
                    }
                    else
                        poll();
                }
            }
        }
        refresh.current = read;
        if (id)
            void read();
        return () => { alive = false; controller.abort(); close(); stopTimer(); if (refresh.current === read)
            refresh.current = null; };
    }, [id, eventSource, schedule, cancel]);
    async function submitTurn(text: string): Promise<SubmitResult> {
        try {
            const result = await bff<{
                conversation_id?: string;
            }>(id ? `/api/conversations/${id}/turns` : "/api/conversations", { method: "POST", body: { request: text } });
            if (id)
                void refresh.current?.();
            return { conversation_id: result.conversation_id };
        }
        catch (e) {
            return { code: apiError(e).code };
        }
    }
    async function refreshView(): Promise<void> { await refresh.current?.(); }
    return { refresh: refreshView, view: snapshot.id === id ? snapshot.view : undefined, blockedCards: snapshot.id === id ? snapshot.blockedCards : [], error: snapshot.id === id ? snapshot.error : undefined, loading: !!id && (snapshot.id !== id || !snapshot.view && !snapshot.error), submitTurn };
}
