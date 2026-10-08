"use client";
import { useRouter } from "next/navigation";
import { Composer } from "@/components/composer";
import { useConversation } from "@/hooks/use-conversation";
export default function NewConversation() {
    const router = useRouter();
    const { submitTurn } = useConversation();
    return <><h1 className="text-xl font-semibold">새 대화</h1><Composer onSubmit={async (text) => { const result = await submitTurn(text); if (!result.code && result.conversation_id)
        router.push(`/c/${result.conversation_id}`); return result; }}/></>;
}
