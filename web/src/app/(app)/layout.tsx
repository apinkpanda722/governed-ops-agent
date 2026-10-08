"use client";
import { useSession } from "@/hooks/use-session";
import { AppShell } from "@/components/app-shell";
import { errorMessage } from "@/lib/messages";
export default function AppLayout({ children }: {
    children: React.ReactNode;
}) {
    const session = useSession();
    if (session.loading)
        return <p role="status" className="p-8">사용자를 확인하는 중입니다…</p>;
    if (session.error)
        return <p role="alert" className="p-8">{errorMessage(session.error.code)}</p>;
    if (!session.user_id)
        return null;
    return <AppShell user={session.user_id}>{children}</AppShell>;
}
