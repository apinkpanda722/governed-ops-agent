import type { Metadata } from "next";
import { AGENT_DESCRIPTION } from "@/lib/messages";
import "./globals.css";

export const metadata: Metadata = {
  title: "해온클라우드 운영 에이전트",
  description: AGENT_DESCRIPTION,
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="ko"
      className="h-full antialiased"
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
