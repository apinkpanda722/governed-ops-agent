"use client";
import { useEffect, useState } from "react";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";
import { Sheet, SheetContent, SheetTitle } from "./ui/sheet";
export function AppShell({user, children}: {user: string; children: React.ReactNode}) {
    const [collapsed, setCollapsed] = useState(false);
    const [mobile, setMobile] = useState(false);
    const [open, setOpen] = useState(false);
    useEffect(() => {
        const media = window.matchMedia("(max-width: 767px)");
        const change = () => { setMobile(media.matches); if (!media.matches) setOpen(false); };
        change();
        media.addEventListener("change", change);
        return () => media.removeEventListener("change", change);
    }, []);
    return <div className="flex h-dvh w-full overflow-hidden">
        {!mobile && <div className="hidden h-full border-r md:block"><Sidebar user={user} collapsed={collapsed} onToggle={() => setCollapsed(value => !value)}/></div>}
        <div className="flex min-w-0 flex-1 flex-col"><Topbar onOpen={() => setOpen(true)}/><main className="min-h-0 flex-1 overflow-y-auto p-4 md:p-8"><div className="mx-auto max-w-4xl space-y-6">{children}</div></main></div>
        {mobile && <Sheet open={open} onOpenChange={setOpen}>{open && <SheetContent side="left" className="w-72! gap-0 p-0" aria-describedby={undefined}><SheetTitle className="sr-only">해온클라우드 사이드바</SheetTitle><Sidebar user={user} collapsed={false} onToggle={() => setOpen(false)}/></SheetContent>}</Sheet>}
    </div>;
}
