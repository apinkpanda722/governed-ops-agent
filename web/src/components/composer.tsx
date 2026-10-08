"use client";
import { useState } from "react";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";
import { ConflictNotice } from "./conflict-notice";
import { errorMessage } from "@/lib/messages";
import { apiError } from "@/lib/bff";
import { testids } from "@/lib/testids";
import type { SubmitResult } from "@/lib/types";
export function Composer({ status, onSubmit }: {
    status?: string;
    onSubmit: (text: string) => Promise<SubmitResult>;
}) {
    const [text, setText] = useState("");
    const [code, setCode] = useState<string>();
    const [submitting, setSubmitting] = useState(false);
    const busy = status === "running" || status === "awaiting_approval";
    async function submit(event: React.FormEvent) {
        event.preventDefault();
        if (busy || submitting || !text.trim() || text.length > 2000)
            return;
        setSubmitting(true);
        setCode(undefined);
        try {
            const result = await onSubmit(text);
            if (result.code)
                setCode(result.code);
            else
                setText("");
        }
        catch (e) {
            setCode(apiError(e).code);
        }
        finally {
            setSubmitting(false);
        }
    }
    return <form onSubmit={submit} className="space-y-3 border-t pt-4">
 <label htmlFor="request-input" className="text-sm font-medium">운영 요청</label>
 <Textarea id="request-input" data-testid={testids.composerInput} maxLength={2000} value={text} onChange={e => setText(e.target.value.slice(0, 2000))} disabled={busy || submitting} placeholder="처리할 업무를 입력해 주세요."/>
 <div className="flex items-center justify-between"><span data-testid={testids.composerCount} className="text-sm text-muted-foreground">{text.length} / 2000</span><Button type="submit" data-testid={testids.composerSubmit} disabled={busy || submitting || !text.trim()}>{submitting ? "접수 중…" : "요청 보내기"}</Button></div>
 {busy && <p className="text-sm" role="status">{errorMessage(status === "running" ? "turn_in_progress" : "approval_pending")}</p>}
 {code && <ConflictNotice code={code}/>}
 </form>;
}
