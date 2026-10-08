"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiError, bff } from "@/lib/bff";
import { errorMessage } from "@/lib/messages";
import { testids } from "@/lib/testids";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
export default function Login() {
    const router = useRouter();
    const [users, setUsers] = useState<string[]>([]);
    const [code, setCode] = useState<string>();
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    useEffect(() => { let active = true; const controller = new AbortController(); void bff<{
        users?: string[];
    }>("/api/demo-users", { signal: controller.signal }).then(v => { if (active) {
        const labels = v.users ?? [];
        setUsers(labels);
        if (!labels.length)
            setCode("request_failed");
    } }).catch(e => { if (active)
        setCode(apiError(e).code); }).finally(() => { if (active)
        setLoading(false); }); return () => { active = false; controller.abort(); }; }, []);
    async function login(user: string) { setBusy(true); setCode(undefined); try {
        await bff("/api/session", { method: "POST", body: { user } });
        router.push("/c");
    }
    catch (e) {
        setCode(apiError(e).code);
    }
    finally {
        setBusy(false);
    } }
    const groups = new Map<string, {
        label: string;
        display: string;
    }[]>();
    for (const label of users) {
        const match = /^demo-(.+)-g(\d+)$/.exec(label);
        const team = match?.[1] ?? "기타";
        groups.set(team, [...(groups.get(team) ?? []), { label, display: match ? `${team} ${match[2]}급` : label }]);
    }
    return <main className="mx-auto w-full max-w-3xl space-y-6 px-4 py-12"><h1 className="text-2xl font-semibold">해온클라우드 운영 에이전트</h1><p>업무를 수행할 데모 사용자를 선택해 주세요.</p>{loading && <p role="status">사용자 목록을 불러오는 중입니다…</p>}{code && <Alert variant="destructive" data-testid={testids.loginError}>{errorMessage(code)}</Alert>}<div className="grid gap-4 sm:grid-cols-2">{[...groups].map(([team, labels]) => <Card key={team}><CardHeader><CardTitle><h2>{team}</h2></CardTitle></CardHeader><CardContent className="flex flex-wrap gap-2">{labels.map(u => <Button key={u.label} variant="outline" data-testid={testids.loginUser(u.label)} disabled={busy} onClick={() => login(u.label)}>{u.display}</Button>)}</CardContent></Card>)}</div></main>;
}
