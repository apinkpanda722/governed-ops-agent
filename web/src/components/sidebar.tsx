"use client";
import Link from "next/link";
import { MessageSquare, Plus, Inbox, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useApprovals } from "@/hooks/use-approvals";
import { testids } from "@/lib/testids";
import { ConversationList } from "./conversation-list";
import { UserCard } from "./user-card";
import { ScrollArea } from "./ui/scroll-area";
import { Separator } from "./ui/separator";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { TooltipProvider } from "./ui/tooltip";
export function Sidebar({user, collapsed, onToggle}: {user: string; collapsed: boolean; onToggle: () => void}) {
    const {approvals} = useApprovals(10000);
    const linkClass = "flex items-center gap-2 rounded-lg p-2 text-sm hover:bg-sidebar-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring";
    return <TooltipProvider><aside data-testid={testids.sidebar} data-collapsed={collapsed} aria-label="사이드바"
        className={`flex h-full shrink-0 flex-col bg-sidebar text-sidebar-foreground ${collapsed ? "w-16" : "w-72"}`}>
        <div className="flex items-center justify-between gap-2 p-3">
            <Link href="/c" aria-label="해온클라우드 운영 에이전트" className={`truncate font-semibold focus-visible:ring-2 focus-visible:ring-ring ${collapsed ? "sr-only" : ""}`}>해온클라우드</Link>
            <Button size="icon" variant="ghost" data-testid={testids.sidebarToggle} aria-label={collapsed ? "사이드바 펼치기" : "사이드바 접기"} aria-expanded={!collapsed} onClick={onToggle}>{collapsed ? <PanelLeftOpen aria-hidden="true"/> : <PanelLeftClose aria-hidden="true"/>}</Button>
        </div>
        <div className="px-3 pb-3"><Button className="w-full" render={<Link href="/c"/>} data-testid={testids.sidebarNewChat} aria-label="새 대화"><Plus aria-hidden="true"/>{!collapsed && "새 대화"}</Button></div>
        <Separator/>
        <ScrollArea className="min-h-0 flex-1">{!collapsed && <ConversationList/>}</ScrollArea>
        <nav aria-label="주 메뉴" className="space-y-1 border-t border-sidebar-border p-3">
            <Link href="/c" className={linkClass} aria-label="대화" title="대화"><MessageSquare className="size-4 shrink-0" aria-hidden="true"/>{!collapsed && "대화"}</Link>
            <Link href="/approvals" className={linkClass} aria-label="승인함" title="승인함" data-testid={testids.navApprovals}><Inbox className="size-4 shrink-0" aria-hidden="true"/>{!collapsed && "승인함"}{approvals.length > 0 && <Badge variant="secondary" data-testid={testids.navApprovalsCount}>{approvals.length}</Badge>}</Link>
        </nav>
        <UserCard user={user} collapsed={collapsed}/>
    </aside></TooltipProvider>;
}
