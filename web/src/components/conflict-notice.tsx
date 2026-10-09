import { TriangleAlert } from "lucide-react";
import { Alert, AlertDescription } from "./ui/alert";
import { errorMessage } from "@/lib/messages";
import { testids } from "@/lib/testids";
export function ConflictNotice({ code }: {
    code: string;
}) { return <Alert variant="destructive" ><TriangleAlert aria-hidden="true"/><AlertDescription data-testid={testids.conflictNotice}>{errorMessage(code)}</AlertDescription></Alert>; }
