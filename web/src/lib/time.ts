export function formatRemaining(seconds: number): string {
    const value = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
    return `${Math.floor(value / 60)}분 ${value % 60}초`;
}
export function formatClock(iso: string, now = new Date()): string {
    const date = new Date(iso);
    if (!Number.isFinite(date.getTime())) return "";
    const clock = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
    const today = date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
    return today ? clock : `${date.getMonth() + 1}월 ${date.getDate()}일 ${clock}`;
}
