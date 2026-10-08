"use client";
import { useApprovals } from "@/hooks/use-approvals";
import { testids } from "@/lib/testids";
import { ApprovalCard } from "./approval-card";
import { ConflictNotice } from "./conflict-notice";
export function ApprovalInbox() {
    const { approvals, loading, error, refresh } = useApprovals();
    return <div className="space-y-4">
        {loading && <p role="status">승인 요청을 불러오는 중입니다…</p>}
        {error && <ConflictNotice code={error.code}/>}
        {!loading && !error && approvals.length === 0 && <p data-testid={testids.inboxEmpty}>대기 중인 승인 요청이 없습니다.</p>}
        {approvals.map((row, i) => <div data-testid={testids.inboxRow} key={row.turn_id ?? i}><ApprovalCard view={{ turn_id: row.turn_id, status: row.status, draft: row.draft, draft_sha256: row.draft_sha256, check: row.check, expires_at: row.expires_at, requester_label: row.requester_label, conversation_id: row.conversation_id }} onChanged={refresh}/></div>)}
    </div>;
}
