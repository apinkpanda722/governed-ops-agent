import type { ApiError } from "./types";
const defaultUnauthenticated = () => window.location.assign("/login");
let onUnauthenticated: () => void = defaultUnauthenticated;
export function setOnUnauthenticated(hook: () => void = defaultUnauthenticated) { onUnauthenticated = hook; }
type Options = {
    method?: "GET" | "POST" | "DELETE";
    body?: unknown;
    signal?: AbortSignal;
    onUnauthenticated?: () => void;
};
export async function bff<T>(path: string, options: Options = {}): Promise<T> {
    if (!/^\/api\/[A-Za-z0-9/_-]+$/.test(path))
        throw { status: 0, code: "invalid_request" } satisfies ApiError;
    let response: Response;
    try {
        response = await fetch(path, {
            method: options.method ?? "GET",
            credentials: "same-origin",
            signal: options.signal,
            ...(options.method && options.method !== "GET" ? {
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(options.body ?? {}),
            } : {}),
        });
    }
    catch {
        throw { status: 0, code: "upstream_unavailable" } satisfies ApiError;
    }
    if (!response.ok) {
        let code = "request_failed";
        try {
            const value = await response.json();
            if (typeof value.code === "string" && /^[a-z_]{1,40}$/.test(value.code))
                code = value.code;
        }
        catch { }
        if (response.status === 401)
            (options.onUnauthenticated ?? onUnauthenticated)();
        throw { status: response.status, code } satisfies ApiError;
    }
    try {
        return await response.json() as T;
    }
    catch {
        throw { status: 502, code: "upstream_unavailable" } satisfies ApiError;
    }
}
export function apiError(error: unknown): ApiError {
    if (error && typeof error === "object" && "status" in error && "code" in error && typeof error.status === "number" && typeof error.code === "string")
        return { status: error.status, code: error.code };
    return { status: 0, code: "upstream_unavailable" };
}
