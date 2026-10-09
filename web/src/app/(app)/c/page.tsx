"use client";
import { useRouter } from "next/navigation";
import { Composer } from "@/components/composer";
import { useConversation } from "@/hooks/use-conversation";
import { useConversationList } from "@/components/conversation-list-context";
import { AGENT_DESCRIPTION } from "@/lib/messages";
export default function NewConversation() {
    const router = useRouter();
    const {submitTurn} = useConversation();
    const list = useConversationList();
    return <div className="mx-auto flex min-h-[60vh] max-w-3xl flex-col justify-center gap-6"><div className="text-center"><h1 className="text-2xl font-semibold">무엇을 도와드릴까요?</h1><p className="mt-3 text-sm text-muted-foreground">{AGENT_DESCRIPTION}</p></div><Composer onSubmit={async text => { const result = await submitTurn(text); if (!result.code && result.conversation_id) { await list.refresh(); router.push(`/c/${result.conversation_id}`); } return result; }}/></div>;
}
