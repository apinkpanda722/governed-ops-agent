import { expect, test } from "vitest";
import { formatClock, formatRemaining } from "./time";
test.each([[0,"0분 0초"],[1,"0분 1초"],[59,"0분 59초"],[60,"1분 0초"],[1721,"28분 41초"],[-2,"0분 0초"],[1.9,"0분 1초"]])("remaining %s", (seconds, expected) => expect(formatRemaining(Number(seconds))).toBe(expected));
test("clock uses local dates", () => {
 const now = new Date(2026, 9, 9, 12);
 expect(formatClock(new Date(2026,9,9,3,4).toISOString(),now)).toBe("03:04");
 expect(formatClock(new Date(2026,9,8,3,4).toISOString(),now)).toBe("10월 8일 03:04");
 expect(formatClock("invalid",now)).toBe("");
});
