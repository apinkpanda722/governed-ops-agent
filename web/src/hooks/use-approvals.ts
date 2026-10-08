"use client";
import { useEffect, useRef, useState } from "react";
import { apiError, bff } from "@/lib/bff";
import type { ApiError, ApprovalRow } from "@/lib/types";
// Only presentation fields enter state, including for another requester's approval.
function rowView(row: ApprovalRow): ApprovalRow {
    return { turn_id: row.turn_id, status: row.status, requester_label: row.requester_label, conversation_id: row.conversation_id, draft: row.draft, draft_sha256: row.draft_sha256, check: row.check, expires_at: row.expires_at };
}
export function useApprovals(interval = 5000) {
    const [approvals, setApprovals] = useState<ApprovalRow[]>([]), [error, setError] = useState<ApiError>();
    const [loading, setLoading] = useState(true);
    const readRef = useRef<(() => Promise<void>) | null>(null);
    useEffect(() => {
        let alive = true, reading = false, again = false, authenticated = true;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const controller = new AbortController();
        const clear = () => { if (timer !== undefined) clearTimeout(timer); timer = undefined; };
        const schedule = () => { if (alive && authenticated && document.visibilityState !== "hidden" && timer === undefined) timer = setTimeout(() => { timer = undefined; void read(); }, interval); };
        async function read() {
            if (!alive || !authenticated) return;
            if (reading) { again = true; return; }
            clear(); reading = true;
            try {
                const value = await bff<{ approvals: ApprovalRow[] }>("/api/approvals", { signal: controller.signal });
                if (alive) { setApprovals((value.approvals ?? []).map(rowView)); setError(undefined); }
            }
            catch (e) { if (alive) { const failure = apiError(e); setError(failure); if (failure.status === 401) authenticated = false; } }
            finally { reading = false; if (alive) { setLoading(false); if (again) { again = false; void read(); } else schedule(); } }
        }
        const visibility = () => { clear(); if (document.visibilityState !== "hidden") void read(); };
        readRef.current = read;
        document.addEventListener("visibilitychange", visibility);
        void read();
        return () => { alive = false; controller.abort(); clear(); document.removeEventListener("visibilitychange", visibility); if (readRef.current === read) readRef.current = null; };
    }, [interval]);
    async function refresh() { await readRef.current?.(); }
    return { approvals, loading, error, refresh };
}
