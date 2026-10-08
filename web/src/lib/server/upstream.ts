import { apiBase } from "./env";
type Options = { method?: string; token?: string; body?: unknown; signal?: AbortSignal };
export async function upstreamFetch(path: string, { method = "GET", token, body, signal }: Options = {}): Promise<Response> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  try {
    return await fetch(`${apiBase()}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: signal ?? AbortSignal.timeout(15000), cache: "no-store", redirect: "error" });
  } catch (error) {
    const name = error instanceof Error && /^[A-Za-z]+Error$/.test(error.name) ? error.name : "UpstreamError";
    const safe = new Error(name); safe.name = name; throw safe;
  }
}
