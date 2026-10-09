import { ApprovalInbox } from "@/components/approval-inbox";
export default function Approvals() {
    return <><h1 className="sr-only">승인함</h1><p className="mb-4 text-sm text-muted-foreground">수신처와 본문을 확인한 뒤 승인하거나 거부해 주세요.</p><ApprovalInbox/></>;
}
