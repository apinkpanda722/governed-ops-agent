"use client";
import { useEffect, useRef, useState } from "react";
import { apiError, bff } from "@/lib/bff";
import { conversationListView, type ApiError, type ConversationListItem } from "@/lib/types";
export function useConversationList() {
    const [items, setItems] = useState<ConversationListItem[]>([]), [error, setError] = useState<ApiError>();
    const [loading, setLoading] = useState(true);
    const readRef = useRef<(() => Promise<void>) | null>(null);
    useEffect(() => {
        let alive = true, reading = false, again = false, authenticated = true;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const controller = new AbortController();
        const clear = () => { if (timer !== undefined) clearTimeout(timer); timer = undefined; };
        const schedule = () => { if (alive && authenticated && document.visibilityState !== "hidden" && timer === undefined) timer = setTimeout(() => { timer = undefined; void read(); }, 10000); };
        async function read() {
            if (!alive || !authenticated) return;
            if (reading) { again = true; return; }
            clear(); reading = true;
            try {
                const value = await bff<{ conversations: unknown }>("/api/conversations", { signal: controller.signal });
                if (alive) { setItems(conversationListView(value.conversations)); setError(undefined); }
            }
            catch (e) { if (alive) { const failure = apiError(e); setError(failure); if (failure.status === 401) authenticated = false; } }
            finally { reading = false; if (alive) { setLoading(false); if (again) { again = false; void read(); } else schedule(); } }
        }
        const visibility = () => { clear(); if (document.visibilityState !== "hidden") void read(); };
        readRef.current = read;
        document.addEventListener("visibilitychange", visibility);
        void read();
        return () => { alive = false; controller.abort(); clear(); document.removeEventListener("visibilitychange", visibility); if (readRef.current === read) readRef.current = null; };
    }, []);
    async function refresh() { await readRef.current?.(); }
    return { items, loading, error, refresh };
}
