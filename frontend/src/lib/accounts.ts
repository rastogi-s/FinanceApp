/** Shapes and helpers for Settings → Accounts. */

export type Account = {
  id: number;
  name: string;
  display_name: string;
  type: string;
  subtype: string | null;
  /** Checking, savings, cd, card...: what it's grouped as (backend kinds.py). */
  kind: string;
  /** What the app guessed from the type, subtype and name. */
  kind_auto: string;
  /** Picked by hand on the account's sheet. */
  kind_set: boolean;
  mask: string | null;
  hidden: boolean;
  is_authoritative: boolean;
  owner_user_id: number | null;
  balance_cents: number | null;
  balance_date: string | null;
  statement_cents: number | null;
  statement_date: string | null;
  statement_diff_percent: number | null;
  /** Uploaded and typed-in accounts only: when the next statement should be uploaded. */
  reminder: Reminder | null;
};
export type Kind = { key: string; label: string; one: string; about: string; owed: boolean };
export type Every = "monthly" | "quarterly" | "weekly" | "off";
export type Reminder = {
  every: Every;
  day: number | null; // the statement's closing day; null: the end of the month
  newest: string; // the newest transaction or balance date
  statement: string | null;
  due: string | null;
  is_due: boolean;
  days_late: number;
  snoozed_until: string | null;
};
/** An upload that's due, as Home shows it. */
export type UploadDue = {
  account_id: number;
  name: string;
  provider: "file" | "manual";
  every: Every;
  newest: string;
  statement: string | null;
  days_late: number;
};

const monthOnly = new Intl.DateTimeFormat("en-US", {
  month: "long",
  timeZone: "UTC",
});
const dayMonth = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

/** "October statement" (or "This week's transactions"), for the account that's due. */
export function dueLabel(u: {
  provider: string;
  every: Every;
  statement: string | null;
}): string {
  if (u.every === "weekly") return "This week's transactions";
  if (u.provider === "manual") return "A new balance";
  return u.statement
    ? `${monthOnly.format(new Date(u.statement))} statement`
    : "A new statement";
}

export function shortDate(iso: string): string {
  return dayMonth.format(new Date(iso));
}
export type Connection = {
  id: number;
  provider: "plaid" | "snaptrade" | "file" | "manual";
  institution: string;
  status: "active" | "stale" | "revoked";
  last_synced_at: string | null;
  last_error: string | null;
  accounts: Account[];
};
export type ConnectionsData = {
  connections: Connection[];
  revoked: Connection[];
  providers: { plaid: boolean; snaptrade: boolean };
  people: { id: number; name: string }[];
  /** In the order the page lists them. */
  kinds: Kind[];
  sync: {
    last_at: string | null;
    last_ok: boolean | null;
    clean_days: number;
    running: boolean;
  };
};

export function parseDollars(text: string): number | null {
  const v = text.replace(/[$,\s]/g, "");
  if (!/^-?\d+(\.\d{0,2})?$/.test(v)) return null;
  return Math.round(parseFloat(v) * 100);
}
