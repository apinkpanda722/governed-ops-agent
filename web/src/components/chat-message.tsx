import { Markdown } from "./markdown";
export function UserBubble({children,testId}: {children: string; testId: string}) { return <div className="flex justify-end"><p data-testid={testId} className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl bg-muted px-4 py-3">{children}</p></div>; }
export function AssistantMessage({children,testId}: {children: string; testId: string}) { return <div data-testid={testId} className="min-w-0 w-full"><Markdown>{children}</Markdown></div>; }
