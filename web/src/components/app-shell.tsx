"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { bff, apiError } from "@/lib/bff";
import { testids } from "@/lib/testids";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { ConflictNotice } from "./conflict-notice";
export function AppShell({ user, children }: {
    user: string;
    children: React.ReactNode;
}) {
    const router = useRouter();
    const [code, setCode] = useState<string>();
    async function switchUser() { try {
        await bff("/api/session", { method: "DELETE" });
        router.replace("/login");
    }
    catch (e) {
        setCode(apiError(e).code);
    } }
    return <div className="mx-auto w-full max-w-4xl px-4"><header className="flex flex-wrap items-center justify-between gap-4 border-b py-4"><Link href="/c" className="font-semibold">해온클라우드 운영 에이전트</Link><nav aria-label="주 메뉴" className="flex items-center gap-4"><Link href="/c" data-testid={testids.navNewConversation}>새 대화</Link><Link href="/approvals" data-testid={testids.navApprovals}>승인함 <Badge variant="secondary" data-testid={testids.navApprovalsCount}/></Link></nav><div className="flex items-center gap-3"><span data-testid={testids.sessionUser}>{user}</span><Button variant="outline" data-testid={testids.switchUser} onClick={switchUser}>사용자 전환</Button></div></header>{code && <ConflictNotice code={code}/>}<main className="space-y-6 py-8">{children}</main></div>;
}
