"use client";
import { Check, ChevronDown, TriangleAlert } from "lucide-react";
import { toolLabel } from "@/lib/messages";
import { testids } from "@/lib/testids";
import type { ToolStep } from "@/lib/types";
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from "./ui/collapsible";
export function ToolSteps({steps,defaultOpen = false}: {steps: ToolStep[]; defaultOpen?: boolean}) {
    const failures = steps.filter(step => !step.ok).length;
    const Icon = failures ? TriangleAlert : Check;
    return <Collapsible defaultOpen={defaultOpen} data-testid={testids.toolSteps} className="text-sm text-muted-foreground">
        <CollapsibleTrigger className="flex items-center gap-2 py-2"><Icon aria-hidden="true" className="size-4"/>{`도구 ${steps.length}단계 사용${failures ? ` (실패 ${failures})` : ""}`}<ChevronDown aria-hidden="true" className="size-4"/></CollapsibleTrigger>
        <CollapsibleContent><div className="space-y-2 border-l pl-4">{steps.map(({name,ok},i) => <div key={i} title={name} data-testid={testids.toolStepRow} className="flex items-center gap-2">{ok ? <Check aria-hidden="true" className="size-4"/> : <TriangleAlert aria-hidden="true" className="size-4"/>}{toolLabel(name)}</div>)}</div></CollapsibleContent>
    </Collapsible>;
}
