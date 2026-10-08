import type { ConversationListItem } from "./types";
type GroupKey = "오늘" | "어제" | "이전";
function dayKey(date: Date, now: Date): GroupKey {
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    const day = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
    return day === today.getTime() ? "오늘" : day === yesterday.getTime() ? "어제" : "이전";
}
export function groupConversations(items: ConversationListItem[], now: Date): {key: GroupKey; items: ConversationListItem[]}[] {
    return (["오늘", "어제", "이전"] as const).map(key => ({
        key, items: items.filter(item => dayKey(new Date(item.updated_at), now) === key)
    })).filter(group => group.items.length > 0);
}
export function formatListTime(updatedAt: string, now: Date): string {
    const date = new Date(updatedAt);
    if (!Number.isFinite(date.getTime())) return "";
    return dayKey(date, now) === "이전" ? `${date.getMonth() + 1}/${date.getDate()}` :
        `${String(date.getHours()).padStart(2,"0")}:${String(date.getMinutes()).padStart(2,"0")}`;
}
