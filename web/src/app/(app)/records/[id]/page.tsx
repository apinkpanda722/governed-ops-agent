"use client";
import { useParams } from "next/navigation";
import { RecordsView } from "@/components/records-view";
export default function Records() {
    const {id} = useParams<{id: string}>();
    return <div className="mx-auto max-w-3xl space-y-6 pb-10"><h1 className="text-xl font-semibold">기록</h1><RecordsView key={id} id={id}/></div>;
}
