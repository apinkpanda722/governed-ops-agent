"use client";
import { useEffect, useState } from "react";
import { useApprovals } from "@/hooks/use-approvals";
import { testids } from "@/lib/testids";
import { userDisplay } from "@/lib/teams";
import { formatRemaining } from "@/lib/time";
import { ApprovalCard } from "./approval-card";
import { ConflictNotice } from "./conflict-notice";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./ui/table";
export function ApprovalInbox() {
    const { approvals, loading, error, refresh } = useApprovals();
    const [now, setNow] = useState(()=>Date.now());
    const [open, setOpen] = useState<Record<string,boolean>>({});
    useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return ()=>clearInterval(timer);},[]);
    return <div className="space-y-4">
        {loading && <p role="status">승인 요청을 불러오는 중입니다…</p>}
        {error && <ConflictNotice code={error.code}/>}
        {!loading && !error && approvals.length === 0 && <p data-testid={testids.inboxEmpty}>대기 중인 승인 요청이 없습니다.</p>}
        {approvals.length > 0 && <Table data-testid={testids.inboxTable}>
            <TableHeader><TableRow>{["요청자","수신처","남은 시간","상태"].map(label=><TableHead key={label}>{label}</TableHead>)}</TableRow></TableHeader>
            {approvals.map((row,i)=>{
                const key=row.turn_id ?? String(i);
                const deadline=Date.parse(row.expires_at ?? "");
                const remaining=Number.isFinite(deadline)?Math.max(0,Math.ceil((deadline-now)/1000)):0;
                return <TableBody data-testid={testids.inboxRow} key={key}>
                    <TableRow>
                        <TableCell><Button variant="ghost" data-testid={testids.inboxRequester} aria-expanded={!!open[key]} onClick={()=>setOpen(v=>({...v,[key]:!v[key]}))}>{userDisplay(row.requester_label ?? "")}</Button></TableCell>
                        <TableCell data-testid={testids.inboxTarget}>{row.draft?.args.channel ?? row.draft?.args.ticket_id ?? row.draft?.args.parent_ticket_id}</TableCell>
                        <TableCell data-testid={testids.inboxRemaining}>{formatRemaining(remaining)}</TableCell>
                        <TableCell><Badge data-testid={testids.inboxStatus} variant={remaining ? "secondary" : "outline"}>{remaining ? "승인 대기" : "만료"}</Badge></TableCell>
                    </TableRow>
                    {open[key] && <TableRow><TableCell colSpan={4} className="whitespace-normal"><ApprovalCard view={{ turn_id: row.turn_id, status: row.status, draft: row.draft, draft_sha256: row.draft_sha256, check: row.check, expires_at: row.expires_at, requester_label: row.requester_label, conversation_id: row.conversation_id, can_decide: row.can_decide }} onChanged={refresh}/></TableCell></TableRow>}
                </TableBody>;
            })}
        </Table>}
    </div>;
}
