"use client";
import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { groupConversations, formatListTime } from "@/lib/conversation-groups";
import { testids } from "@/lib/testids";
import { errorMessage } from "@/lib/messages";
import { useConversationList } from "./conversation-list-context";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
export function ConversationList() {
    const {items, loading, error} = useConversationList();
    const path = usePathname();
    const [expanded, setExpanded] = useState(false);
    const now = new Date();
    if (loading && !items.length) return <p role="status" className="p-3 text-sm text-muted-foreground">대화를 불러오는 중입니다…</p>;
    if (error) return <p role="alert" className="p-3 text-sm text-warning">{errorMessage(error.code)}</p>;
    if (!items.length) return <p className="p-3 text-sm text-muted-foreground">아직 대화가 없습니다.</p>;
    return <div className="space-y-4 p-2">
        {groupConversations(items.slice(0, expanded ? 50 : 20), now).map(group => <section key={group.key}>
            <h2 data-testid={testids.conversationGroup(group.key)} className="px-2 pb-2 text-xs font-medium text-muted-foreground">{group.key}</h2>
            <ul className="space-y-1">{group.items.map(item => <li key={item.conversation_id}>
                <Link href={`/c/${item.conversation_id}`} data-testid={testids.conversationItem(item.conversation_id)}
                    aria-current={path === `/c/${item.conversation_id}` ? "page" : undefined}
                    className="block rounded-lg p-2 hover:bg-sidebar-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring aria-[current=page]:bg-sidebar-accent">
                    <div className="flex items-center gap-2"><span className="min-w-0 flex-1 truncate text-sm">{item.title}</span><time dateTime={item.updated_at} className="shrink-0 text-xs text-muted-foreground">{formatListTime(item.updated_at, now)}</time></div>
                    {item.awaiting_approval && <Badge variant="secondary" className="mt-1 text-warning" data-testid={testids.conversationPending(item.conversation_id)}>승인 대기</Badge>}
                </Link>
            </li>)}</ul>
        </section>)}
        {!expanded && items.length > 20 && <Button variant="ghost" className="w-full" data-testid={testids.conversationMore} onClick={() => setExpanded(true)}>더 보기</Button>}
    </div>;
}
