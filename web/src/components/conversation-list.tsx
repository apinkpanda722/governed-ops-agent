"use client";
import { useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { groupConversations, formatListTime } from "@/lib/conversation-groups";
import { testids } from "@/lib/testids";
import { DELETE_CONFIRM_NOTICE, errorMessage } from "@/lib/messages";
import { apiError, bff } from "@/lib/bff";
import type { ConversationListItem } from "@/lib/types";
import { useConversationList } from "./conversation-list-context";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
function ConversationRow({item, path, now, refresh}: {item: ConversationListItem; path: string; now: Date; refresh: () => Promise<void>}) {
    const router = useRouter();
    const [confirming, setConfirming] = useState(false), [deleting, setDeleting] = useState(false);
    const [error, setError] = useState<string>();
    const pending = useRef(false);
    const id = item.conversation_id;
    const confirmId = `delete-confirm-${id}`, noticeId = `delete-notice-${id}`;
    async function remove() {
        if (pending.current) return;
        pending.current = true; setDeleting(true); setError(undefined);
        try {
            const result = await bff<{deleted: boolean}>(`/api/conversations/${id}`, {method: "DELETE"});
            if (result.deleted !== true) throw {status: 502, code: "upstream_unavailable"};
            setConfirming(false);
            await refresh();
            if (path === `/c/${id}`) router.replace("/c");
        }
        catch (e) { setError(apiError(e).code); }
        finally { pending.current = false; setDeleting(false); }
    }
    return <li>
        <div className="flex items-center gap-1">
            <Link href={`/c/${id}`} data-testid={testids.conversationItem(id)}
                aria-current={path === `/c/${id}` ? "page" : undefined}
                className="min-w-0 flex-1 rounded-lg p-2 hover:bg-sidebar-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring aria-[current=page]:bg-sidebar-accent">
                <div className="flex items-center gap-2"><span className="min-w-0 flex-1 truncate text-sm">{item.title}</span><time dateTime={item.updated_at} className="shrink-0 text-xs text-muted-foreground">{formatListTime(item.updated_at, now)}</time></div>
                {item.awaiting_approval && <Badge variant="secondary" className="mt-1 text-warning" data-testid={testids.conversationPending(id)}>승인 대기</Badge>}
            </Link>
            <Button type="button" variant="ghost" size="xs" data-testid={testids.conversationDelete(id)} aria-label={`${item.title} 삭제`}
                aria-expanded={confirming} aria-controls={confirming ? confirmId : undefined} disabled={deleting}
                onClick={() => { setError(undefined); setConfirming(true); }}>삭제</Button>
        </div>
        {confirming && <div id={confirmId} role="group" aria-label="대화 삭제 확인" aria-describedby={noticeId} className="space-y-2 rounded-lg border p-2">
            <p id={noticeId} className="text-sm">{DELETE_CONFIRM_NOTICE}</p>
            <div className="flex gap-2">
                <Button type="button" variant="destructive" size="sm" data-testid={testids.conversationDeleteConfirm(id)} disabled={deleting} onClick={() => void remove()}>{deleting ? "삭제 중…" : "삭제 확인"}</Button>
                <Button type="button" variant="outline" size="sm" data-testid={testids.conversationDeleteCancel(id)} disabled={deleting} onClick={() => { setConfirming(false); setError(undefined); }}>취소</Button>
            </div>
        </div>}
        {error && <p role="alert" className="p-2 text-sm text-warning">{errorMessage(error)}</p>}
    </li>;
}
export function ConversationList() {
    const {items, loading, error, refresh} = useConversationList();
    const path = usePathname();
    const [expanded, setExpanded] = useState(false);
    const now = new Date();
    if (loading && !items.length) return <p role="status" className="p-3 text-sm text-muted-foreground">대화를 불러오는 중입니다…</p>;
    if (error) return <p role="alert" className="p-3 text-sm text-warning">{errorMessage(error.code)}</p>;
    if (!items.length) return <p className="p-3 text-sm text-muted-foreground">아직 대화가 없습니다.</p>;
    return <div className="space-y-4 p-2">
        {groupConversations(items.slice(0, expanded ? 50 : 20), now).map(group => <section key={group.key}>
            <h2 data-testid={testids.conversationGroup(group.key)} className="px-2 pb-2 text-xs font-medium text-muted-foreground">{group.key}</h2>
            <ul className="space-y-1">{group.items.map(item => <ConversationRow key={item.conversation_id} item={item} path={path} now={now} refresh={refresh}/>)}</ul>
        </section>)}
        {!expanded && items.length > 20 && <Button variant="ghost" className="w-full" data-testid={testids.conversationMore} onClick={() => setExpanded(true)}>더 보기</Button>}
    </div>;
}
