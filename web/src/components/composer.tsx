"use client";
import { useLayoutEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";
import { ConflictNotice } from "./conflict-notice";
import { COMPOSER_NOTICE, errorMessage } from "@/lib/messages";
import { apiError } from "@/lib/bff";
import { testids } from "@/lib/testids";
import type { SubmitResult } from "@/lib/types";
export function Composer({status,onSubmit}: {status?: string; onSubmit: (text: string) => Promise<SubmitResult>}) {
    const [text,setText] = useState("");
    const [code,setCode] = useState<string>();
    const [submitting,setSubmitting] = useState(false);
    const locked = useRef(false);
    const input = useRef<HTMLTextAreaElement>(null);
    const busy = status === "running" || status === "awaiting_approval";
    useLayoutEffect(() => { const el = input.current; if (el) { el.style.height = "auto"; el.style.height = `${Math.min(el.scrollHeight,240)}px`; } },[text]);
    async function submit() {
        if (busy || locked.current || !text.trim() || text.length > 2000) return;
        locked.current = true; setSubmitting(true); setCode(undefined);
        try { const result = await onSubmit(text); if (result.code) setCode(result.code); else setText(""); }
        catch(e) { setCode(apiError(e).code); }
        finally { locked.current = false; setSubmitting(false); }
    }
    return <div className="space-y-2"><form onSubmit={event => {event.preventDefault(); void submit();}} className="space-y-3 rounded-2xl border bg-card p-4 shadow-lg">
        <label htmlFor="request-input" className="sr-only">운영 요청</label>
        <Textarea ref={input} id="request-input" data-testid={testids.composerInput} maxLength={2000} value={text} onChange={e => setText(e.target.value.slice(0,2000))} disabled={busy || submitting} placeholder="처리할 업무를 입력해 주세요." className="max-h-60 min-h-16 resize-none overflow-y-auto border-0 shadow-none" onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) { event.preventDefault(); void submit(); } }}/>
        <div className="flex items-center gap-3"><span data-testid={testids.composerCount} className="text-xs text-muted-foreground">{text.length} / 2000</span>{busy && <p className="flex-1 text-xs" role="status">{errorMessage(status === "running" ? "turn_in_progress" : "approval_pending")}</p>}<Button type="submit" size="icon" aria-label="전송" className="ml-auto" data-testid={testids.composerSubmit} disabled={busy || submitting || !text.trim()}><Send aria-hidden="true"/></Button></div>
        {code && <ConflictNotice code={code}/>}
    </form><p data-testid={testids.composerHint} className="text-center text-xs text-muted-foreground">{COMPOSER_NOTICE}</p></div>;
}
