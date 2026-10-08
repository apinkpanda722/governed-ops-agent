"use client";
import { createContext, useContext } from "react";
import { useConversationList as useList } from "@/hooks/use-conversation-list";
const ListContext = createContext<ReturnType<typeof useList> | null>(null);
export function ConversationListProvider({children}: {children: React.ReactNode}) {
    const value = useList();
    return <ListContext.Provider value={value}>{children}</ListContext.Provider>;
}
export function useConversationList() {
    const value = useContext(ListContext);
    if (!value) throw new Error("ConversationListProvider is required");
    return value;
}
