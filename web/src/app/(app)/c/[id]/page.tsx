"use client";
import { useEffect, useRef } from "react";
import { useParams } from "next/navigation";
import { useConversation } from "@/hooks/use-conversation";
import { useStayAtBottom } from "@/hooks/use-stay-at-bottom";
import { useConversationList } from "@/components/conversation-list-context";
import { Thread } from "@/components/thread";
import { Composer } from "@/components/composer";
import { ConflictNotice } from "@/components/conflict-notice";
export default function Conversation() {
    const {id} = useParams<{id: string}>();
    const c = useConversation(id);
    const {refresh: refreshList} = useConversationList();
    const listRefresh = useRef(refreshList);
    useEffect(() => { listRefresh.current = refreshList; },[refreshList]);
    const status = c.view?.turn?.status;
    useEffect(() => { if (status) void listRefresh.current(); },[id,status]);
    const scroll = useRef<HTMLDivElement>(null);
    const bottom = useRef(true);
    useEffect(() => { const el = scroll.current; if (el && bottom.current) el.scrollTop = el.scrollHeight; },[c.view,c.blockedCards]);
    useStayAtBottom(scroll, bottom);
    return <div className="mx-auto flex h-[calc(100dvh-10rem)] min-h-0 max-w-3xl flex-col"><div ref={scroll} onScroll={() => { const el = scroll.current; if (el) bottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }} className="min-h-0 flex-1 overflow-y-auto pb-10">
        {c.loading && <p role="status">대화를 불러오는 중입니다…</p>}{c.error && <ConflictNotice code={c.error.code}/>}<Thread onChanged={c.refresh} view={c.view} blockedCards={c.blockedCards}/>
    </div><div className="sticky bottom-0 shrink-0 bg-background pt-3"><Composer status={status} onSubmit={async text => { const result = await c.submitTurn(text); if (!result.code) { bottom.current = true; await listRefresh.current(); } return result; }}/></div></div>;
}
