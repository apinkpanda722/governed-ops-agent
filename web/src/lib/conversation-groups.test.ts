import { expect,test } from "vitest";
import {groupConversations,formatListTime} from "./conversation-groups";
import type {ConversationListItem} from "./types";
const row=(date:Date,id:string):ConversationListItem=>({conversation_id:id,title:id,status:"completed",awaiting_approval:false,turn_count:1,updated_at:date.toISOString()});
test("local day groups omit empty groups and respect midnight",()=>{
 const now=new Date(2026,9,8,0,1);
 const items=[row(new Date(2026,9,8,0,0),"today"),row(new Date(2026,9,7,23,59),"yesterday"),row(new Date(2026,9,6,23,59),"older")];
 expect(groupConversations(items,now).map(g=>[g.key,g.items[0].title])).toEqual([["오늘","today"],["어제","yesterday"],["이전","older"]]);
 expect(groupConversations([items[1]],now).map(g=>g.key)).toEqual(["어제"]);expect(groupConversations([],now)).toEqual([]);
});
test("formats local time and invalid dates",()=>{
 const now=new Date(2026,9,8,14,0);
 expect(formatListTime(new Date(2026,9,8,9,5).toISOString(),now)).toBe("09:05");
 expect(formatListTime(new Date(2026,9,7,23,59).toISOString(),now)).toBe("23:59");
 expect(formatListTime(new Date(2026,8,30).toISOString(),now)).toBe("9/30");
 expect(formatListTime("invalid",now)).toBe("");
});
