import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Composer } from "./composer";
import { errorMessage } from "@/lib/messages";
afterEach(cleanup);
test.each(["approval_pending", "turn_in_progress", "turn_limit", "invalid_request"])("keeps failed input and displays %s", async (code) => {
    render(<Composer onSubmit={vi.fn().mockResolvedValue({ code })}/>);
    const input = screen.getByTestId("composer-input") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "normal request" } });
    fireEvent.click(screen.getByTestId("composer-submit"));
    await waitFor(() => expect(screen.getByTestId("conflict-notice").textContent).toBe(errorMessage(code)), { timeout: 1000 });
    expect(input.value).toBe("normal request");
});
test("limits length, blocks whitespace and clears successful input", async () => {
    const submit = vi.fn().mockResolvedValue({});
    render(<Composer onSubmit={submit}/>);
    const input = screen.getByTestId("composer-input") as HTMLTextAreaElement;
    expect(input.maxLength).toBe(2000);
    fireEvent.change(input, { target: { value: "   " } });
    expect((screen.getByTestId("composer-submit") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input, { target: { value: "x".repeat(2001) } });
    expect(input.value.length).toBe(2000);
    fireEvent.click(screen.getByTestId("composer-submit"));
    await waitFor(() => expect(input.value).toBe(""), { timeout: 1000 });
});
test.each(["running", "awaiting_approval"])("disables input while %s", status => {
    render(<Composer status={status} onSubmit={vi.fn()}/>);
    expect((screen.getByTestId("composer-input") as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByTestId("composer-submit") as HTMLButtonElement).disabled).toBe(true);
});
