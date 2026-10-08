import { TriangleAlert } from "lucide-react";
import { Card, CardContent } from "./ui/card";
import { Badge } from "./ui/badge";
import { blockCategory } from "@/lib/messages";
import { testids } from "@/lib/testids";
import type { BlockView } from "@/lib/types";
export function BlockedCard({ code, message }: BlockView) {
    return <Card data-testid={testids.blockedCard}><CardContent className="space-y-2"><TriangleAlert aria-hidden="true" className="size-5 text-destructive"/><Badge variant="destructive" data-testid={testids.blockedCardCategory}>{blockCategory(code)}</Badge><p data-testid={testids.blockedCardMessage}>{message}</p></CardContent></Card>;
}
