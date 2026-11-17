/** Low cash reminders: a payment made on a schedule from one account, and whether it has the cash (backend funding.py). */
export type Every = "week" | "2weeks" | "month";

export type FundingRow = {
  id: number;
  name: string;
  account_id: number;
  account: string;
  amount_cents: number;
  every: Every;
  start: string;
  cushion_cents: number;
  ahead_days: number;
  on: boolean;
  /** "Every Thursday", "Every other Monday", "Every month on the 15th". */
  schedule: string;
  next: string;
  days_left: number;
  state: "ok" | "short" | "unknown" | "off";
  cash_cents: number | null;
  cash_as_of: string | null;
  cash_from: "holdings" | "available" | "balance" | "none";
  /** What to add before `next`: every payment from the account through that day, plus the cushion, less the cash. */
  short_cents: number;
  needed_cents: number;
  /** Other reminders on the same account counted in what's needed. */
  with_others: string[];
  href: string;
};

/** Buys that repeat in a brokerage, offered as a reminder to add with one tap. */
export type FundingSuggestion = {
  key: string;
  account_id: number;
  account: string;
  name: string;
  amount_cents: number;
  every: Every;
  start: string;
  schedule: string;
  times: number;
  last: string;
};

export type FundingAccount = { id: number; name: string; type: string; cash_cents: number | null; cash_as_of: string | null };

export type FundingState = {
  today: string;
  reminders: FundingRow[];
  suggestions: FundingSuggestion[];
  accounts: FundingAccount[];
};

export type FundingIn = {
  name: string;
  account_id: number;
  amount_cents: number;
  every: Every;
  start: string;
  cushion_cents: number;
  ahead_days: number;
  on: boolean;
};

const weekday = new Intl.DateTimeFormat("en-US", { weekday: "long", timeZone: "UTC" });
const short = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });

/** "today", "tomorrow", "Thursday" (this week), "Thu, Nov 13" (later). */
export function whenLabel(iso: string, daysLeft: number) {
  const d = new Date(iso + "T12:00:00Z");
  if (daysLeft <= 0) return "today";
  if (daysLeft === 1) return "tomorrow";
  if (daysLeft < 7) return weekday.format(d);
  return short.format(d);
}
