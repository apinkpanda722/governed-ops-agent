export function envOr(name: string, fallback: string): string {
  return process.env[name]?.trim() || fallback;
}
export function apiBase(): string {
  const value = envOr("GOVOPS_API_URL", "http://127.0.0.1:8000");
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new Error("InvalidApiBase");
  }
  return url.origin;
}
