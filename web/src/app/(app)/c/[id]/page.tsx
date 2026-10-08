"use client";
import { useParams } from "next/navigation";
import { useConversation } from "@/hooks/use-conversation";
import { Thread } from "@/components/thread";
import { Composer } from "@/components/composer";
import { ConflictNotice } from "@/components/conflict-notice";
export default function Conversation() {
    const { id } = useParams<{
        id: string;
    }>();
    const c = useConversation(id);
    return <><h1 className="text-xl font-semibold">운영 대화</h1>{c.loading && <p role="status">대화를 불러오는 중입니다…</p>}{c.error && <ConflictNotice code={c.error.code}/>}<Thread onChanged={c.refresh} view={c.view} blockedCards={c.blockedCards}/><Composer status={c.view?.turn?.status} onSubmit={c.submitTurn}/></>;
}
