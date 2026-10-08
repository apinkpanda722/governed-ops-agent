import { Alert, AlertDescription } from "./ui/alert";
import { errorMessage } from "@/lib/messages";
import { testids } from "@/lib/testids";
export function ConflictNotice({ code }: {
    code: string;
}) { return <Alert variant="destructive" data-testid={testids.conflictNotice}><AlertDescription>{errorMessage(code)}</AlertDescription></Alert>; }
