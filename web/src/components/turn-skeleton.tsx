import { Skeleton } from "./ui/skeleton";
import { testids } from "@/lib/testids";
export function TurnSkeleton() { return <div data-testid={testids.turnSkeleton} aria-busy="true" className="space-y-3"><Skeleton className="h-4 w-full"/><Skeleton className="h-4 w-5/6"/><Skeleton className="h-4 w-2/3"/></div>; }
