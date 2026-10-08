import type { TurnView as Turn } from "@/lib/types";
import { testids } from "@/lib/testids";
import { statusMessage } from "@/lib/messages";
import { Badge } from "./ui/badge";
import { BlockedCard } from "./blocked-card";
import { QuarantineNote } from "./quarantine-note";
import { ApprovalSlot } from "./approval-slot";
export function TurnView({ turn, hideBlock = false, onChanged }: {
    turn: Turn;
    hideBlock?: boolean;
    onChanged?: () => void | Promise<void>;
}) {
    if (turn.status === "blocked_input" || turn.status === "blocked")
        return hideBlock ? null : <BlockedCard code={turn.block?.code} message={turn.block?.message}/>;
    const message = statusMessage(turn.status, turn.retry_hint);
    return <section className="space-y-3" aria-live="polite">
 {turn.request && <p className="ml-auto w-fit max-w-full whitespace-pre-wrap rounded-xl bg-muted p-3" data-testid={testids.turnRequest}>{turn.request}</p>}
 {message && <p data-testid={testids.turnStatus}>{message}</p>}
 {turn.status === "running" && <span role="progressbar" aria-label="답변 작성 중" className="inline-block size-3 animate-pulse rounded-full bg-primary"/>}
 {turn.status === "completed" && <><p className="whitespace-pre-wrap" data-testid={testids.turnAnswer}>{turn.answer}</p><div className="flex flex-wrap gap-2">{turn.cited_doc_ids?.map((id, i) => <Badge variant="outline" key={`${id}-${i}`} data-testid={testids.turnCitedDoc}>{id}</Badge>)}</div></>}
 {(turn.guardrail?.quarantined ?? 0) > 0 && <QuarantineNote quarantined={turn.guardrail!.quarantined!} quarantined_at_least={turn.guardrail?.quarantined_at_least}/>}
 {["failed", "rejected", "expired", "interrupted"].includes(turn.status ?? "") && <p data-testid={testids.turnError}>{message}</p>}
 <ApprovalSlot turn={turn} onChanged={onChanged}/>
 </section>;
}
