import { Alert, AlertDescription } from "./ui/alert";
import { Badge } from "./ui/badge";
import { testids } from "@/lib/testids";
export function QuarantineNote({ quarantined, quarantined_at_least = false }: {
    quarantined: number;
    quarantined_at_least?: boolean;
}) {
    return <Alert data-testid={testids.quarantineNote}><Badge variant="secondary">참고 문서 일부 격리</Badge><AlertDescription>보안 검사에서 일부 참고 문서를 제외하고 답변했습니다. ({quarantined}건{quarantined_at_least ? " 이상" : ""})</AlertDescription></Alert>;
}
