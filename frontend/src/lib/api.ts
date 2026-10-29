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

// Names people know for the fields the server checks, so a rejected form says which part to fix.
const FIELD_NAMES: Record<string, string> = {
  gross: "Gross pay",
  taxes: "Taxes",
  savings: "Savings",
  net: "Take-home pay",
  employer_match: "Employer 401(k) match",
  match: "Employer 401(k) match",
  employer_hsa: "Employer HSA money",
  employer_hsa_seed: "Employer HSA yearly deposit",
  hsa: "HSA",
  espp: "ESPP",
  roth: "Roth 401(k)",
  retirement: "401(k)",
  federal: "Federal income tax",
  state: "State income tax",
  social_security: "Social Security",
  period_start: "Pay period start",
  period_end: "Pay period end",
  mask: "Account's last four digits",
};

type Problem = { loc?: (string | number)[]; msg?: string; type?: string };

function fieldName(loc: (string | number)[]): string | null {
  const parts = loc.filter((p) => p !== "body");
  const key = parts.findLast((p) => typeof p === "string");
  if (key === undefined) return null;
  const bare = String(key).replace(/_cents$/, "");
  const name = FIELD_NAMES[bare] ?? bare.charAt(0).toUpperCase() + bare.slice(1).replaceAll("_", " ");
  // Year-to-date amounts sit beside this paycheck's, so say which column.
  return parts.includes("ytd") ? `${name} (year to date)` : name;
}

function problemText(p: Problem): string {
  const msg = (p.msg ?? "").replace(/^Value error, /, "");
  const name = fieldName(p.loc ?? []);
  if (!name) return msg || "Some of that isn't quite right.";
  if (p.type === "extra_forbidden") return `${name} isn't something the app can save yet.`;
  if (p.type === "missing") return `${name} is missing.`;
  if (p.type === "greater_than_equal") return `${name} can't be negative.`;
  if (p.type === "greater_than") return `${name} has to be more than zero.`;
  if (p.type === "less_than_equal" || p.type === "less_than") return `${name} is too large.`;
  if (p.type?.startsWith("date")) return `${name} isn't a date.`;
  return `${name}: ${msg || "isn't quite right"}.`;
}

/** The first thing the server didn't accept, named so the person knows where to look. */
export function validationMessage(problems: Problem[]): string {
  if (!problems[0]) return "Some of that isn't quite right. Check it and try again.";
  const first = problemText(problems[0]);
  const more = problems.length - 1;
  return more ? `${first} ${more === 1 ? "One more thing needs" : `${more} more things need`} a look too.` : first;
}

async function detail(r: Response): Promise<string> {
  try {
    const body = await r.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return validationMessage(body.detail);
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
