"use client";
import { useEffect, useState, useSyncExternalStore } from "react";
import { applyTheme, readTheme, writeTheme, type ThemeMode } from "@/lib/theme";
const subscribe = () => () => {};
const serverTheme = (): ThemeMode => "system";
export function useTheme() {
    const saved = useSyncExternalStore(subscribe, readTheme, serverTheme);
    const [chosen, updateMode] = useState<ThemeMode>();
    const mode = chosen ?? saved;
    useEffect(() => {
        applyTheme(mode);
        if (mode !== "system") return;
        const media = window.matchMedia("(prefers-color-scheme: dark)");
        const change = () => applyTheme("system");
        media.addEventListener("change", change);
        return () => media.removeEventListener("change", change);
    }, [mode]);
    function setMode(next: ThemeMode) { writeTheme(next); updateMode(next); applyTheme(next); }
    return { mode, setMode };
}
