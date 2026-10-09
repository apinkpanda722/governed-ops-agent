export type ThemeMode = "light" | "dark" | "system";
export function readTheme(): ThemeMode {
    try {
        const value = localStorage.getItem("govops-theme");
        return value === "light" || value === "dark" || value === "system" ? value : "system";
    } catch { return "system"; }
}
export function writeTheme(mode: ThemeMode): boolean {
    try {
        if (!["light", "dark", "system"].includes(mode)) return false;
        localStorage.setItem("govops-theme", mode);
        return true;
    } catch { return false; }
}
export function applyTheme(mode: ThemeMode): void {
    const dark = mode === "dark" || (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", dark);
}
