import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { Markdown } from "./markdown";
afterEach(cleanup);
test("renders GFM elements and a scrolling table", () => {
    const {container} = render(<Markdown>{"# 제목\n\n**굵게** `inline`\n\n- 목록\n\n```\nblock\n```\n\n| a | b |\n| :- | -: |\n| c | d |"}</Markdown>);
    for (const selector of ["h1", "strong", "ul li", "code", "pre code", "table", "th", "td"]) expect(container.querySelector(selector)).not.toBeNull();
    expect(container.querySelector("table")!.parentElement!.className).toContain("overflow-x-auto");
});
test("untrusted markdown cannot create executable elements or leaking images", () => {
    const {container} = render(<Markdown>{'<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n![x](https://example.invalid/p.png?q=secret)\n\n[javascript](javascript:alert(1)) [data](data:text/html,x) [relative](/c/1) [vb](vbscript:foo) [safe](https://example.invalid)\n\n<iframe src="https://example.invalid">\n\n<a href="javascript:alert(1)">x</a>\n\n<style>body{color:red}</style>\n\n<object></object><embed><form><input></form>\n\n- [x] task'}</Markdown>);
    expect(container.querySelectorAll("script,img,iframe,style,object,embed,form,input")).toHaveLength(0);
    expect(container.querySelectorAll("a")).toHaveLength(1);
    for (const el of container.querySelectorAll("*")) for (const attr of el.attributes) expect(attr.name).not.toMatch(/^on|^style$/i);
    for (const a of container.querySelectorAll("a")) { expect(a.href).toMatch(/^https?:/); expect(a.rel).toContain("noopener"); expect(a.target).toBe("_blank"); }
    for (const label of ["javascript", "data", "relative", "vb"]) { expect(container.textContent).toContain(label); expect([...container.querySelectorAll("a")].some(a => a.textContent === label)).toBe(false); }
});
