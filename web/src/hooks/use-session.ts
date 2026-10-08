"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiError, bff } from "@/lib/bff";
import type { ApiError } from "@/lib/types";
export function useSession() {
    const router = useRouter();
    const [user, setUser] = useState<string>();
    const [error, setError] = useState<ApiError>();
    const [loading, setLoading] = useState(true);
    useEffect(() => {
        let active = true;
        const controller = new AbortController();
        void bff<{
            user_id: string;
        }>("/api/session", { signal: controller.signal, onUnauthenticated: () => router.replace("/login") }).then(v => { if (active)
            setUser(v.user_id); }).catch(e => { if (active)
            setError(apiError(e)); }).finally(() => { if (active)
            setLoading(false); });
        return () => { active = false; controller.abort(); };
    }, [router]);
    return { user_id: user, loading, error };
}
