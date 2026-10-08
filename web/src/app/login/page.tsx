"use client";
import { AlertTriangle } from "lucide-react";
import { TEAMS, TEAM_ORDER, parseUserLabel } from "@/lib/teams";
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

    const groups = new Map<string, {label: string; display: string}[]>();
    for (const label of users) {
        const parsed = parseUserLabel(label);
        const team = parsed && Object.hasOwn(TEAMS, parsed.team) ? parsed.team : "기타";
        groups.set(team, [...(groups.get(team) ?? []), {label, display: parsed ? `${parsed.grade}급` : label}]);
    }
    const order = [...TEAM_ORDER, "기타"].filter(team => groups.has(team));
    return <main className="flex min-h-dvh items-center justify-center bg-background px-4 py-10">
        <Card className="w-full max-w-3xl shadow-sm">
            <CardHeader><CardTitle><h1 className="text-2xl font-semibold">해온클라우드 운영 에이전트</h1></CardTitle>
                <p className="text-sm text-muted-foreground">데모 로그인 — 이 PC에서만 사용할 수 있습니다.</p>
                <p className="text-sm">업무를 수행할 데모 사용자를 선택해 주세요.</p></CardHeader>
            <CardContent className="space-y-6">
                {loading && <p role="status">사용자 목록을 불러오는 중입니다…</p>}
                {code && <Alert variant="destructive" role="alert" data-testid={testids.loginError}><AlertTriangle aria-hidden="true"/><span>{errorMessage(code)}</span></Alert>}
                <div className="grid gap-4 sm:grid-cols-2">{order.map(team => <section key={team} className="rounded-lg border p-4">
                    <h2 className="mb-3 font-medium">{TEAMS[team] ?? "기타"}</h2>
                    <div className="flex flex-wrap gap-2">{groups.get(team)!.map(u => <Button key={u.label} variant="outline" title={u.label} data-testid={testids.loginUser(u.label)} disabled={busy} onClick={() => login(u.label)}>{u.display}</Button>)}</div>
                </section>)}</div>
            </CardContent>
        </Card>
    </main>;
}
