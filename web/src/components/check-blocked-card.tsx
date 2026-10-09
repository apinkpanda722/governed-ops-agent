import { TriangleAlert } from "lucide-react";
import { Card, CardContent } from "./ui/card";
import { Badge } from "./ui/badge";
import { testids } from "@/lib/testids";
export function CheckBlockedCard({message}: {message: string}) { return <Card data-testid={testids.checkBlockedCard}><CardContent className="space-y-2"><TriangleAlert aria-hidden="true" className="size-5 text-destructive"/><Badge variant="destructive">발송 차단</Badge><p>{message}</p></CardContent></Card>; }
