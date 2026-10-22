import type { TurnView } from "@/lib/types";
import { SentCard } from "./sent-card";
import { ApprovalCard } from "./approval-card";
export function ApprovalSlot({ turn, onChanged }: { turn: TurnView; onChanged?: () => void | Promise<void> }) {
    if (turn.status === "awaiting_approval" && turn.draft && turn.draft_sha256)
        return <ApprovalCard view={{ turn_id: turn.turn_id, status: turn.status, draft: turn.draft, draft_sha256: turn.draft_sha256, check: turn.check, expires_at: turn.expires_at, rejected_edit: turn.rejected_edit, effect: turn.effect, can_decide: turn.can_decide }} onChanged={onChanged ?? (() => {})}/>;
    if (turn.status === "completed" && turn.effect)
        return <SentCard turn={turn}/>;
    return null;
}
