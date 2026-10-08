"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { apiError } from "@/lib/bff";
import { errorMessage } from "@/lib/messages";
import { testids } from "@/lib/testids";
import type { TurnView, ApprovalRow, Draft } from "@/lib/types";
import { Button } from "./ui/button";
export type ApprovalView = Pick<TurnView, "turn_id" | "status" | "draft" | "draft_sha256" | "check" | "expires_at" | "rejected_edit" | "effect"> & Pick<ApprovalRow, "requester_label" | "conversation_id">;
const fields: Record<Draft["tool"], ("text" | "body" | "title" | "description")[]> = {
    slack_send_message: ["text"], jira_add_comment: ["body"], jira_create_followup_ticket: ["title", "description"],
};
type Props = { view: ApprovalView; onChanged: () => void | Promise<void> };
export function ApprovalCard(props: Props) {
    if (props.view.status !== "awaiting_approval") return null;
    return <ApprovalCardContent key={`${props.view.turn_id}:${props.view.draft_sha256}:${props.view.rejected_edit?.message ?? ""}`} {...props}/>;
}
function ApprovalCardContent({ view, onChanged }: Props) {
    const [now, setNow] = useState(() => Date.now());
    const [editing, setEditing] = useState(false), [values, setValues] = useState<Record<string, string>>({});
    const [pending, setPending] = useState(false), [result, setResult] = useState("");
    const [settled, setSettled] = useState(false);
    const submitting = useRef(false), notified = useRef<string | undefined>(undefined);
    const changed = useRef(onChanged);
    useEffect(() => { changed.current = onChanged; }, [onChanged]);
    const deadline = Date.parse(view.expires_at ?? "");
    const remaining = Number.isFinite(deadline) ? Math.max(0, Math.ceil((deadline - now) / 1000)) : 0;
    useEffect(() => {
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, []);
    useEffect(() => {
        if (remaining === 0 && view.expires_at && notified.current !== view.expires_at && view.status === "awaiting_approval") {
            notified.current = view.expires_at;
            void changed.current();
        }
    }, [remaining, view.expires_at, view.status]);
    const draft = view.draft;
    if (!draft || !view.draft_sha256 || !view.turn_id || view.status !== "awaiting_approval") return null;
    const editable = fields[draft.tool];
    const edited = Object.fromEntries(editable.flatMap(field => {
        const value = values[field];
        return value?.trim() && value !== (draft.args[field] ?? "") ? [[field, value]] : [];
    }));
    const disabled = pending || settled || remaining === 0;
    async function decide(decision: "approve" | "reject" | "edit_approve") {
        if (submitting.current || disabled || (decision === "edit_approve" && !Object.keys(edited).length)) return;
        submitting.current = true; setPending(true); setResult("");
        try {
            const response = await fetch(`/api/tasks/${view.turn_id}/approval`, {
                method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ decision, draft_sha256: view.draft_sha256, ...(decision === "edit_approve" ? { edited } : {}) }),
            });
            const value: TurnView & { code?: string } = await response.json();
            if (!response.ok) throw { status: response.status, code: value.code ?? "request_failed" };
            if (response.status === 202) setResult("발송을 처리하는 중입니다…");
            else if (value.status === "completed") setResult(`처리했습니다${value.effect?.mock_id ? ` ${value.effect.mock_id}` : ""}`);
            else if (value.status === "rejected") setResult("거부했습니다");
            setSettled(value.status !== "awaiting_approval");
        }
        catch (e) { setResult(errorMessage(apiError(e).code)); }
        finally {
            submitting.current = false; setPending(false);
            await changed.current();
        }
    }
    return <section data-testid={testids.approvalCard} className="space-y-3 rounded-xl border bg-card p-4" aria-live="polite">
        {view.requester_label && <p data-testid={testids.approvalRequester}>{view.requester_label}</p>}
        {view.conversation_id && <Link data-testid={testids.approvalOpenConversation} href={`/c/${view.conversation_id}`}>대화 열기</Link>}
        <p data-testid={testids.approvalTarget}>{draft.args.channel ?? draft.args.ticket_id ?? draft.args.parent_ticket_id}</p>
        <div data-testid={testids.approvalBody} className="whitespace-pre-wrap">{editable.map(field => <p key={field}>{draft.args[field]}</p>)}</div>
        <p data-testid={testids.approvalCheck}>{view.check?.message}</p>
        <p data-testid={testids.approvalExpires}>남은 시간 {remaining}초</p>
        {view.rejected_edit && <p data-testid={testids.approvalRejectedEdit}>수정한 내용이 검사를 통과하지 못했습니다: {view.rejected_edit.message}</p>}
        {remaining === 0 && <p data-testid={testids.approvalExpired}>승인 시간이 지났습니다</p>}
        {result && <p data-testid={testids.approvalResult}>{result}</p>}
        <div className="flex flex-wrap gap-2">
            <Button data-testid={testids.approvalApprove} disabled={disabled} onClick={() => void decide("approve")}>승인</Button>
            <Button variant="outline" data-testid={testids.approvalReject} disabled={disabled} onClick={() => void decide("reject")}>거부</Button>
            <Button variant="outline" data-testid={testids.approvalEditToggle} disabled={disabled} onClick={() => { setEditing(!editing); setValues(Object.fromEntries(editable.map(field => [field, draft.args[field] ?? ""]))); }}>수정 후 승인</Button>
        </div>
        {editing && <div className="space-y-3">{editable.map(field => <label className="block" key={field}>{field}<textarea className="block w-full rounded border p-2" data-testid={testids.approvalEditInput(field)} value={values[field] ?? ""} disabled={disabled} maxLength={4000} onChange={e => setValues(v => ({ ...v, [field]: e.target.value }))}/></label>)}<Button data-testid={testids.approvalEditSubmit} disabled={disabled || !Object.keys(edited).length} onClick={() => void decide("edit_approve")}>수정 내용 승인</Button></div>}
    </section>;
}
