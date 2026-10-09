"use client";
import { useEffect, type RefObject } from "react";
// 입력창이 커지면 스크롤 영역이 줄어드는데 scrollTop은 그대로라 맨 아래 내용이 가려진다. 맨 아래에 있던 경우 크기 변화를 따라 맨 아래를 유지한다.
export function useStayAtBottom(scroll: RefObject<HTMLElement | null>, pinned: RefObject<boolean>) {
    useEffect(() => {
        const el = scroll.current;
        if (!el || typeof ResizeObserver === "undefined") return;
        const observer = new ResizeObserver(() => { if (pinned.current) el.scrollTop = el.scrollHeight; });
        observer.observe(el);
        return () => observer.disconnect();
    }, [scroll, pinned]);
}
