import { expect,test } from "vitest";
import { TEAMS,TEAM_ORDER,parseUserLabel,userDisplay } from "./teams";
test("eight teams and display order",()=>{
 expect(TEAM_ORDER).toEqual(["platform","payments","data-ai","sre","security","hr","finance","legal"]);
 expect(Object.values(TEAMS)).toEqual(["플랫폼개발팀","결제서비스팀","데이터AI팀","SRE인프라팀","정보보안팀","인사팀","재무팀","법무컴플라이언스팀"]);
});
test("labels parse and unknown labels remain unchanged",()=>{
 expect(parseUserLabel("demo-data-ai-g3")).toEqual({team:"data-ai",grade:3});
 expect(userDisplay("demo-finance-g2")).toBe("재무팀 · 2급");
 expect(userDisplay("demo-other-g2")).toBe("demo-other-g2");expect(userDisplay("odd")).toBe("odd");
 expect(parseUserLabel("odd")).toBeNull();
});
