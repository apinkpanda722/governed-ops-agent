import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { Button } from "@/components/ui/button";

afterEach(cleanup);

test("renders the button text", () => {
  render(<Button>확인</Button>);

  expect(screen.getByRole("button", { name: "확인" }).textContent).toBe("확인");
});
