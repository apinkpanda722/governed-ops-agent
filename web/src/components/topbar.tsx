"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, Plus } from "lucide-react";
import { AGENT_DESCRIPTION } from "@/lib/messages";
import { testids } from "@/lib/testids";
import { useConversationList } from "./conversation-list-context";
import { Button } from "./ui/button";
export function Topbar({onOpen}: {onOpen: () => void}) {
    const path = usePathname();
    const {items} = useConversationList();
    const id = /^\/c\/([^/]+)$/.exec(path)?.[1];
    const title = path === "/approvals" ? "승인함" : items.find(item => item.conversation_id === id)?.title ?? "새 대화";
    return <header className="flex shrink-0 items-center gap-3 border-b bg-background px-4 py-3">
        <Button size="icon" variant="ghost" className="md:hidden" aria-label="사이드바 열기" data-testid={testids.sidebarOpen} onClick={onOpen}><Menu aria-hidden="true"/></Button>
        <div className="min-w-0 flex-1"><h1 className="truncate font-semibold" data-testid={testids.topbarTitle}>{title}</h1>
            <p className="hidden truncate text-xs text-muted-foreground md:block" data-testid={testids.agentDescription}>{AGENT_DESCRIPTION}</p></div>
        <Button variant="outline" nativeButton={false} render={<Link href="/c"/>} data-testid={testids.navNewConversation}><Plus aria-hidden="true"/>새 대화</Button>
    </header>;
}
