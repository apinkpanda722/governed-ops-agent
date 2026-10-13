import { TriangleAlert } from "lucide-react";
import type { TurnView as Turn } from "@/lib/types";
import { testids } from "@/lib/testids";
import { APPROVED_RUN_NOTICE, GENERIC_CHECK_BLOCK, LIMITED_NOTICE, statusMessage } from "@/lib/messages";
import { Badge } from "./ui/badge";
import { BlockedCard } from "./blocked-card";
import { CheckBlockedCard } from "./check-blocked-card";
import { QuarantineNote } from "./quarantine-note";
import { ApprovalSlot } from "./approval-slot";
import { UserBubble, AssistantMessage } from "./chat-message";
import { ToolSteps } from "./tool-steps";
import { TurnSkeleton } from "./turn-skeleton";
export function TurnView({ turn, hideBlock = false, onChanged }: {turn: Turn; hideBlock?: boolean; onChanged?: () => void | Promise<void>}) {
    const message = statusMessage(turn.status, turn.retry_hint, turn.error);
    return <section className="min-w-0 space-y-4">
        {turn.request && !["blocked_input","blocked"].includes(turn.status ?? "") && <UserBubble testId={testids.turnRequest}>{turn.request}</UserBubble>}
        {["running","awaiting_approval"].includes(turn.status ?? "") && <p data-testid={testids.turnStatus}>{message}</p>}
        {!!turn.tool_steps?.length && <ToolSteps key={turn.status === "running" ? "running" : "settled"} steps={turn.tool_steps.map(({name,ok}) => ({name,ok}))} defaultOpen={turn.status === "running"}/>}
        {turn.status === "running" && <><TurnSkeleton/><p role="status" aria-live="polite" className="sr-only">{message}</p></>}
        {turn.status === "completed" && <>{!(turn.effect && turn.answer === APPROVED_RUN_NOTICE) && <AssistantMessage testId={testids.turnAnswer}>{turn.answer ?? ""}</AssistantMessage>}<div className="flex flex-wrap gap-2">{turn.cited_doc_ids?.map((id,i) => <Badge variant="outline" key={`${id}-${i}`} data-testid={testids.turnCitedDoc}>{id}</Badge>)}</div></>}
        {turn.status === "completed" && turn.limited === true && <p data-testid={testids.turnLimited} className="text-sm text-muted-foreground">{LIMITED_NOTICE}</p>}
        {(turn.guardrail?.quarantined ?? 0) > 0 && <QuarantineNote quarantined={turn.guardrail!.quarantined!} quarantined_at_least={turn.guardrail?.quarantined_at_least}/>}
        {["failed","rejected","expired","interrupted"].includes(turn.status ?? "") && <div role="alert" className="flex gap-3 rounded-xl border border-destructive p-4"><TriangleAlert aria-hidden="true" className="size-5 shrink-0 text-destructive"/><p data-testid={testids.turnError}>{message}</p></div>}
        {turn.status === "blocked_input" && !hideBlock && <BlockedCard code={turn.block?.code} message={turn.block?.message}/>}
        {turn.status === "blocked" && <CheckBlockedCard message={turn.check?.message || turn.answer || GENERIC_CHECK_BLOCK}/>}
        <ApprovalSlot turn={turn} onChanged={onChanged}/>
    </section>;
}
