import { expect, test } from "@playwright/test";
import { testids } from "../src/lib/testids";
import { loginAs, switchTo, ORIGIN } from "./support";

test("승인 완료 기록의 PASS·발송 이벤트와 타인·삭제 대화 접근 차단", async ({ page }) => {
  await loginAs(page, "demo-platform-g3");
  await page.getByTestId(testids.composerInput).fill("INC-2026-012 장애와 관련 티켓을 조회해 주세요");
  await page.getByTestId(testids.composerSubmit).click();
  await expect(page.getByTestId(testids.turnAnswer)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId(testids.turnCitedDoc).first()).toBeVisible();
  const id = new URL(page.url()).pathname.split("/").at(-1)!;

  await page.getByTestId(testids.composerInput).fill("방금 내용을 팀 채널에 공유해줘");
  await page.getByTestId(testids.composerSubmit).click();
  await expect(page.getByTestId(testids.approvalCard)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId(testids.approvalTarget)).toHaveText("team-platform");
  await page.getByTestId(testids.approvalApprove).click();
  await expect(page.getByTestId(testids.sentCard)).toBeVisible({ timeout: 15_000 });

  const recordsPath = `/records/${id}`;
  await expect(page.getByTestId(testids.recordsLink)).toHaveAttribute("href", recordsPath);
  await page.getByTestId(testids.recordsLink).click();
  await expect(page).toHaveURL(`${ORIGIN}${recordsPath}`);
  await expect(page.getByTestId(testids.recordsVerify).getByText("PASS", { exact: true })).toBeVisible();
  await expect(page.getByTestId(testids.recordsTurn)).toHaveCount(2);
  const events = page.getByTestId(testids.recordsEvent);
  const attempt = events.filter({ hasText: "발송 시도" });
  const result = events.filter({ hasText: "발송 결과" });
  await expect(attempt).toHaveCount(1);
  await expect(attempt).toContainText("slack_send_message");
  await expect(result).toHaveCount(1);
  await expect(result).toContainText("결과 ID:");

  await switchTo(page, "demo-finance-g3");
  await page.goto(recordsPath);
  await expect(page.getByTestId(testids.recordsNotFound)).toContainText("기록을 찾을 수 없습니다");
  await expect(page.getByTestId(testids.recordsVerify)).toHaveCount(0);
  await expect(events).toHaveCount(0);

  await switchTo(page, "demo-platform-g3");
  await page.goto(`/c/${id}`);
  await expect(page.getByTestId(testids.sentCard)).toBeVisible();
  await page.getByTestId(testids.conversationDelete(id)).click();
  await page.getByTestId(testids.conversationDeleteConfirm(id)).click();
  await expect(page).toHaveURL(`${ORIGIN}/c`);
  await expect(page.getByTestId(testids.conversationItem(id))).toHaveCount(0);
  await page.goto(recordsPath);
  await expect(page.getByTestId(testids.recordsNotFound)).toContainText("기록을 찾을 수 없습니다");
  await expect(page.getByTestId(testids.recordsVerify)).toHaveCount(0);
  await expect(events).toHaveCount(0);
});
