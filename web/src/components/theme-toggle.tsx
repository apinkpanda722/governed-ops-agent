"use client";
import { Sun, Moon, Monitor } from "lucide-react";
import { useTheme } from "@/hooks/use-theme";
import { testids } from "@/lib/testids";
import { Button } from "./ui/button";
export function ThemeToggle({collapsed = false}: {collapsed?: boolean}) {
    const {mode, setMode} = useTheme();
    return <div aria-label="화면 테마" className={`flex gap-1 ${collapsed ? "flex-col" : ""}`}>
        {([{mode:"light", label:"라이트", Icon:Sun}, {mode:"dark", label:"다크", Icon:Moon}, {mode:"system", label:"시스템", Icon:Monitor}] as const).map(choice =>
            <Button key={choice.mode} size={collapsed ? "icon" : "sm"} variant={mode === choice.mode ? "secondary" : "ghost"}
                aria-label={choice.label} aria-pressed={mode === choice.mode} title={choice.label} data-testid={testids.themeToggle(choice.mode)} onClick={() => setMode(choice.mode)}>
                <choice.Icon aria-hidden="true"/>{!collapsed && choice.label}
            </Button>)}
    </div>;
}
