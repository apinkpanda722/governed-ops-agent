"use client";
import { useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { approvalMethodLabel, sentResultLabel } from "@/lib/messages";
import { formatClock } from "@/lib/time";
import { testids } from "@/lib/testids";
import type { TurnView } from "@/lib/types";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
export function SentCard({ turn }: { turn: TurnView }) {
    const [expanded, setExpanded] = useState(false);
    const tool = turn.draft?.tool ?? "";
    const args = turn.draft?.args;
    const target = args?.channel ?? args?.ticket_id ?? args?.parent_ticket_id;
    const body = (tool === "slack_send_message" ? [args?.text] : tool === "jira_add_comment" ? [args?.body] : tool === "jira_create_followup_ticket" ? [args?.title, args?.description] : []).filter(Boolean).join("\n");
    const long = body.length > 200 || body.split("\n").length > 6;
    const title = ({slack_send_message:"발송 완료",jira_add_comment:"코멘트 등록 완료",jira_create_followup_ticket:"티켓 생성 완료"} as Record<string,string>)[tool] ?? "처리 완료";
    const at = turn.effect_at ? formatClock(turn.effect_at) : "";
    return <Card data-testid={testids.sentCard}>
        <CardHeader><CardTitle className="flex items-center gap-2"><CheckCircle2 aria-hidden="true" className="size-4 text-green-600"/>{title}</CardTitle></CardHeader>
        <CardContent><dl className="space-y-3">
            {target && <div><dt className="text-muted-foreground">수신처</dt><dd data-testid={testids.sentTarget}>{target}</dd></div>}
            {turn.approval_method && <div><dt className="text-muted-foreground">처리 방식</dt><dd data-testid={testids.sentMethod}>{approvalMethodLabel(turn.approval_method)}</dd></div>}
            {turn.effect?.mock_id && <div><dt className="text-muted-foreground">결과 ID</dt><dd data-testid={testids.sentResultId}>{sentResultLabel(tool) || "결과 ID"}: {turn.effect.mock_id}</dd></div>}
            {at && <div><dt className="text-muted-foreground">시각</dt><dd data-testid={testids.sentAt}>{at}</dd></div>}
            {body && <div><dt className="text-muted-foreground">본문</dt><dd data-testid={testids.sentBody} className="whitespace-pre-wrap break-words" style={long && !expanded ? {display:"-webkit-box",WebkitLineClamp:6,WebkitBoxOrient:"vertical",overflow:"hidden"} : undefined}>{body}</dd>{long && <Button variant="ghost" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{expanded ? "접기" : "펼치기"}</Button>}</div>}
        </dl></CardContent>
    </Card>;
}
