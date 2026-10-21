"use client";
import { useEffect, useState } from "react";
import { apiError, bff } from "@/lib/bff";
import type { ApiError, AuditEvent, AuditView, AuditVerifyView } from "@/lib/types";
import { auditActorLabel, auditCodeLabel, auditEventTitle, errorMessage, toolLabel, RECORDS_NOT_FOUND, RECORDS_TAIL_NOTICE, RECORDS_HOST_NOTICE, RECORDS_TRUNCATED_NOTICE } from "@/lib/messages";
import { testids } from "@/lib/testids";

type Snapshot = {id: string; audit?: AuditView; verify?: AuditVerifyView; auditError?: ApiError; verifyError?: ApiError};
export function RecordsView({id}: {id: string}) {
    const [snapshot, setSnapshot] = useState<Snapshot>();
    useEffect(() => {
        let alive = true;
        const controller = new AbortController();
        async function read() {
            const [audit, verify] = await Promise.allSettled([
                bff<AuditView>(`/api/conversations/${id}/audit`, {signal: controller.signal}),
                bff<AuditVerifyView>("/api/audit/verify", {signal: controller.signal}),
            ]);
            if (alive) setSnapshot({id,
                ...(audit.status === "fulfilled" ? {audit: audit.value} : {auditError: apiError(audit.reason)}),
                ...(verify.status === "fulfilled" ? {verify: verify.value} : {verifyError: apiError(verify.reason)}),
            });
        }
        void read();
        return () => {alive = false; controller.abort();};
    }, [id]);
    const current = snapshot?.id === id ? snapshot : undefined;
    if (!current) return <p role="status">기록을 불러오는 중입니다…</p>;
    if (current.auditError?.status === 404) return <p data-testid={testids.recordsNotFound}>{RECORDS_NOT_FOUND}</p>;
    if (current.auditError) return <p role="alert">{errorMessage(current.auditError.code)}</p>;
    const verify = current.verify;
    const turns = [...(current.audit?.turns ?? [])].sort((a,b) => a.turn_index - b.turn_index);
    return <div className="space-y-6">
        {verify?.status && <section data-testid={testids.recordsVerify} aria-label="체인 검증" className="space-y-2 rounded-lg border p-4">
            <p className="font-semibold"><span className={verify.status === "pass" ? "text-green-700 dark:text-green-400" : "text-red-700 dark:text-red-400"}>{verify.status === "pass" ? "PASS" : "FAIL"}</span> · 레코드 {verify.chain?.records ?? 0}개 · 고정점 {verify.chain?.anchors_checked ?? 0}개</p>
            <p className="text-sm">{RECORDS_TAIL_NOTICE}</p><p className="text-sm">{RECORDS_HOST_NOTICE}</p>
            <p className="text-sm text-muted-foreground">확인 시각: {verify.checked_at}</p>
            {(verify.chain?.problems ?? []).map((problem,i) => <p key={i}>{auditCodeLabel(problem.code)}{problem.seq !== undefined ? ` · #${problem.seq}` : ""}</p>)}
        </section>}
        {current.verifyError && <p role="alert">검증 결과를 불러오지 못했습니다. {errorMessage(current.verifyError.code)}</p>}
        {current.audit?.truncated && <p role="status">{RECORDS_TRUNCATED_NOTICE}</p>}
        {turns.map(turn => <section key={turn.turn_id} data-testid={testids.recordsTurn} className="space-y-3">
            <h2 className="font-semibold">턴 {turn.turn_index}</h2>
            <ol className="space-y-3 border-l pl-4">{[...(turn.events ?? [])].sort((a,b) => (a.seq ?? 0) - (b.seq ?? 0)).map((event,i) => <EventRow key={i} event={event}/>)}</ol>
            {!turn.events?.length && <p className="text-sm text-muted-foreground">이 턴의 기록이 없습니다.</p>}
        </section>)}
        {!turns.length && <p>표시할 기록이 없습니다.</p>}
    </div>;
}
function EventRow({event}: {event: AuditEvent}) {
    return <li data-testid={testids.recordsEvent} data-seq={event.seq} className="space-y-1 break-words text-sm">
        <p className="font-medium">#{event.seq} · {auditEventTitle(event.event ?? "", event.check_result)}</p>
        <p><time>{event.ts}</time> · {auditActorLabel(event.event ?? "")}: {event.actor}</p>
        {event.tool && <p>도구: {toolLabel(event.tool)}</p>}
        {event.tool_name && <p>도구: {event.tool_name === "unknown" ? "알 수 없는 도구" : toolLabel(event.tool_name)}</p>}
        {event.status && <p>결과: {auditCodeLabel(event.status)}</p>}
        {event.result_count !== undefined && <p>결과 수: {event.result_count}</p>}
        {event.decision && <p>결정: {auditCodeLabel(event.decision)}</p>}
        {event.check_result && <p>검사: {auditCodeLabel(event.check_result)}</p>}
        {event.reason && <p>사유: {auditCodeLabel(event.reason)}</p>}
        {event.code && <p>코드: {auditCodeLabel(event.code)}</p>}
        {event.mock_id && <p>결과 ID: {event.mock_id}</p>}
        {event.fallback !== undefined && <p>대체 모델: {event.fallback ? "사용" : "미사용"}</p>}
        {event.cost_usd !== undefined && <p>비용: ${event.cost_usd}</p>}
        {event.hash12 && <p>내용 해시: {event.hash12}</p>}
        {event.draft_hash12 && <p>초안 해시: {event.draft_hash12}</p>}
    </li>;
}
