"use client";

/**
 * Talking to the server. Every change carries the session's CSRF token; a 401 means the session ended (idle
 * timeout or signed out elsewhere), and "reauth_required" means the server wants a fresh "it's you" check first,
 * which the app asks for and then retries.
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

let csrfToken: string | null = null;
export function setCsrf(token: string | null) {
  csrfToken = token;
}

type Handlers = { signedOut: () => void; reauth: () => Promise<boolean> };
const handlers: Handlers = { signedOut: () => {}, reauth: async () => false };
export function setApiHandlers(h: Partial<Handlers>) {
  Object.assign(handlers, h);
}

async function detail(r: Response): Promise<string> {
  try {
    const body = await r.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return "Some of that isn't quite right. Check it and try again.";
  } catch {}
  return `Something went wrong (${r.status}). Try again.`;
}

export async function api<T = unknown>(
  path: string,
  options: { method?: string; body?: unknown; form?: FormData; retried?: boolean; quiet401?: boolean } = {},
): Promise<T> {
  const method = options.method ?? (options.body !== undefined || options.form ? "POST" : "GET");
  const headers: Record<string, string> = {};
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let body: BodyInit | undefined;
  if (options.form) body = options.form;
  else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  let r: Response;
  try {
    r = await fetch(path, { method, headers, body, credentials: "same-origin", cache: "no-store" });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (r.ok) return (r.status === 204 ? undefined : await r.json()) as T;
  const message = await detail(r);
  if (r.status === 401 && !options.quiet401) handlers.signedOut();
  if (r.status === 403 && message === "reauth_required" && !options.retried) {
    if (await handlers.reauth()) return api<T>(path, { ...options, retried: true });
    throw new ApiError(403, "Cancelled.");
  }
  throw new ApiError(r.status, message);
}
