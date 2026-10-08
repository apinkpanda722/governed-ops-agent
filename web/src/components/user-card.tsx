"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { apiError, bff } from "@/lib/bff";
import { parseUserLabel, TEAMS, userDisplay } from "@/lib/teams";
import { testids } from "@/lib/testids";
import { ConflictNotice } from "./conflict-notice";
import { ThemeToggle } from "./theme-toggle";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";
export function UserCard({user, collapsed = false}: {user: string; collapsed?: boolean}) {
    const router = useRouter();
    const [code, setCode] = useState<string>();
    const [busy, setBusy] = useState(false);
    const parsed = parseUserLabel(user);
    const team = parsed && Object.hasOwn(TEAMS, parsed.team) ? TEAMS[parsed.team] : undefined;
    async function logout() {
        setBusy(true); setCode(undefined);
        try { await bff("/api/session", {method:"DELETE"}); router.replace("/login"); }
        catch (e) { setCode(apiError(e).code); }
        finally { setBusy(false); }
    }
    return <div data-testid={testids.userCard} className="space-y-3 border-t border-sidebar-border p-3">
        <ThemeToggle collapsed={collapsed}/>
        <div className={`flex items-center gap-2 ${collapsed ? "flex-col" : ""}`}>
            <span aria-hidden="true" className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-sidebar-accent">{(team ?? user).slice(0,1)}</span>
            <div className={`min-w-0 flex-1 ${collapsed ? "sr-only" : ""}`}>
                <Tooltip><TooltipTrigger render={<span tabIndex={0} data-testid={testids.sessionUser} title={user} className="block truncate text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"/>}>{userDisplay(user)}</TooltipTrigger><TooltipContent>{user}</TooltipContent></Tooltip>
                {team && <Badge variant="secondary" className="mt-1" data-testid={testids.userTeamBadge}>{team}</Badge>}
            </div>
            <Button variant="ghost" size="icon" aria-label="로그아웃" title="로그아웃" data-testid={testids.logout} disabled={busy} onClick={logout}><LogOut aria-hidden="true"/></Button>
        </div>
        {code && <ConflictNotice code={code}/>}
    </div>;
}
