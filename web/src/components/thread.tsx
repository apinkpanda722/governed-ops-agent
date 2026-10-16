import Link from "next/link";
import { UserBubble, AssistantMessage } from "./chat-message";
import { Fragment } from "react";
import type { BlockedCardView, ConversationView } from "@/lib/types";
import { testids } from "@/lib/testids";
import { BlockedCard } from "./blocked-card";
import { TurnView } from "./turn-view";
export function Thread({ view, blockedCards = [], onChanged }: {
    view?: ConversationView;
    blockedCards?: BlockedCardView[];
    onChanged?: () => void | Promise<void>;
}) {
    const history = view?.history ?? [];
    const cardsAt = (position: number) => blockedCards.filter(c => Math.min(c.afterMessages, history.length) === position).map(c => <BlockedCard key={c.turn_id} code={c.block.code} message={c.block.message}/>);
    return <div data-testid={testids.thread} className="space-y-5">
 {view?.conversation_id && <Link data-testid={testids.recordsLink} className="inline-block text-sm underline" href={`/records/${view.conversation_id}`}>기록</Link>}
 {cardsAt(0)}{history.map((h, i) => <Fragment key={i}>{h.role === "user" ? <UserBubble testId={testids.historyUser}>{h.content}</UserBubble> : <AssistantMessage testId={testids.historyAssistant}>{h.content}</AssistantMessage>}{cardsAt(i + 1)}</Fragment>)}
 {view?.turn && <TurnView onChanged={onChanged} turn={view.turn} hideBlock={blockedCards.some(c => c.turn_id === view.turn?.turn_id)}/>}
 </div>;
}
