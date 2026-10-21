import type { Transaction } from "@/lib/household";

/** What the Spending page is showing. Kept in the address, so a link (from Budget, say) opens it already filtered. */
export type Filters = {
  q: string;
  categories: string[]; // category ids, or "none" for uncategorized
  accounts: number[];
  kind: "" | "out" | "in" | "transfer" | "review";
  min: string; // dollars, as typed
  max: string;
  range: Range;
  start: string; // yyyy-mm-dd, for a custom range
  end: string;
  sort: Sort;
};

/** "" is newest first, the usual order. */
export type Sort = "" | "oldest" | "largest" | "smallest" | "merchant";

export type Range = "" | "month" | "last-month" | "3-months" | "year" | "custom";

export type Results = {
  transactions: Transaction[];
  count: number;
  out_cents: number;
  in_cents: number;
  first: string | null;
  last: string | null;
  next_offset: number | null;
  accounts: { id: number; name: string }[];
};

export const RANGES: { id: Range; label: string }[] = [
  { id: "", label: "Any time" },
  { id: "month", label: "This month" },
  { id: "last-month", label: "Last month" },
  { id: "3-months", label: "Last 3 months" },
  { id: "year", label: "This year" },
  { id: "custom", label: "Pick dates" },
];

export const SORTS: { id: Sort; label: string; chip: string }[] = [
  { id: "", label: "Newest", chip: "Newest first" },
  { id: "oldest", label: "Oldest", chip: "Oldest first" },
  { id: "largest", label: "Largest", chip: "Largest first" },
  { id: "smallest", label: "Smallest", chip: "Smallest first" },
  { id: "merchant", label: "A to Z", chip: "A to Z" },
];

/** Sorted by date, the list reads best split into days; by anything else, one list with the date on each row. */
export const byDate = (f: Filters) => f.sort === "" || f.sort === "oldest";

export const KINDS: { id: Filters["kind"]; label: string }[] = [
  { id: "", label: "All" },
  { id: "out", label: "Money out" },
  { id: "in", label: "Money in" },
  { id: "transfer", label: "Transfers" },
];

export const EMPTY: Filters = { q: "", categories: [], accounts: [], kind: "", min: "", max: "", range: "", start: "", end: "", sort: "" };

const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** The first and last day a range covers, in the phone's own time zone. */
export function dates(f: Filters, now = new Date()): { start?: string; end?: string } {
  const y = now.getFullYear();
  const m = now.getMonth();
  switch (f.range) {
    case "month":
      return { start: iso(new Date(y, m, 1)) };
    case "last-month":
      return { start: iso(new Date(y, m - 1, 1)), end: iso(new Date(y, m, 0)) };
    case "3-months":
      return { start: iso(new Date(y, m - 3, now.getDate())) };
    case "year":
      return { start: iso(new Date(y, 0, 1)) };
    case "custom":
      return { start: f.start || undefined, end: f.end || undefined };
    default:
      return {};
  }
}

export function cents(text: string): number | null {
  const t = text.replace(/[$,\s]/g, "");
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 ? Math.round(n * 100) : null;
}

export function fromParams(p: URLSearchParams): Filters {
  const kind = p.get("kind") ?? (p.get("review") ? "review" : "");
  const range = p.get("range") ?? "";
  const sort = p.get("sort") ?? "";
  return {
    q: (p.get("q") ?? "").slice(0, 100),
    categories: p.getAll("category").filter((c) => c === "none" || /^\d{1,9}$/.test(c)),
    accounts: p
      .getAll("account")
      .filter((a) => /^\d{1,9}$/.test(a))
      .map(Number),
    kind: (["out", "in", "transfer", "review"].includes(kind) ? kind : "") as Filters["kind"],
    min: p.get("min") ?? "",
    max: p.get("max") ?? "",
    range: (RANGES.some((r) => r.id === range) ? range : "") as Range,
    start: /^\d{4}-\d{2}-\d{2}$/.test(p.get("start") ?? "") ? p.get("start")! : "",
    end: /^\d{4}-\d{2}-\d{2}$/.test(p.get("end") ?? "") ? p.get("end")! : "",
    sort: (SORTS.some((s) => s.id === sort) ? sort : "") as Sort,
  };
}

/** For the address bar: only what's set, in a readable order. */
export function toParams(f: Filters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  f.categories.forEach((c) => p.append("category", c));
  f.accounts.forEach((a) => p.append("account", String(a)));
  if (f.kind) p.set("kind", f.kind);
  if (f.min) p.set("min", f.min);
  if (f.max) p.set("max", f.max);
  if (f.range) p.set("range", f.range);
  if (f.range === "custom" && f.start) p.set("start", f.start);
  if (f.range === "custom" && f.end) p.set("end", f.end);
  if (f.sort) p.set("sort", f.sort);
  return p;
}

/** For the server: dates worked out, dollars turned into cents. */
export function toQuery(f: Filters, offset = 0): string {
  const p = new URLSearchParams();
  if (f.q.trim()) p.set("q", f.q.trim());
  f.categories.forEach((c) => p.append("category", c));
  f.accounts.forEach((a) => p.append("account", String(a)));
  if (f.kind) p.set("kind", f.kind);
  const lo = cents(f.min);
  const hi = cents(f.max);
  if (lo !== null) p.set("min_cents", String(lo));
  if (hi !== null) p.set("max_cents", String(hi));
  const { start, end } = dates(f);
  if (start) p.set("start", start);
  if (end) p.set("end", end);
  if (f.sort) p.set("sort", f.sort);
  if (offset) p.set("offset", String(offset));
  return p.toString();
}

/** How many filters are set in the sheet (the search box, the quick chips and the sort don't count). */
export function sheetCount(f: Filters): number {
  return (
    (f.categories.length ? 1 : 0) +
    (f.accounts.length ? 1 : 0) +
    (f.kind && f.kind !== "review" ? 1 : 0) +
    (f.min || f.max ? 1 : 0) +
    (f.range ? 1 : 0)
  );
}

export const isEmpty = (f: Filters) => toParams(f).toString() === "";
