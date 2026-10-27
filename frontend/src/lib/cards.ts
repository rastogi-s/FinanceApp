"use client";

import type { Offers, RankedOffer } from "@/lib/offers";

/** The Credit Cards page (backend cards.py): your cards, the best card by category, the store lookup, missed points. */

export type Ranked = {
  account_id: number;
  card: string;
  product: string;
  color: string;
  rate: string;
  percent: number;
  value_cents: number;
  why: string;
  bonus: boolean;
  cap: string | null;
  capped: boolean;
  portal: string | null;
  warnings: string[];
  blocked: boolean;
  /** 2: your pick for this category, 1: your default card. Breaks ties. */
  favor: number;
  /** The card's offer at this store (Chase Offers and the like), already counted in value_cents. */
  offer?: RankedOffer;
};

export type Product = {
  id: string;
  name: string;
  short: string;
  issuer: string;
  network: string;
  color: string;
  annual_fee: number;
  choices: { id: string; label: string }[];
};

export type ProductDetail = Product & {
  program: string;
  foreign_fee: boolean;
  earn: {
    rate: string;
    what: string;
    portal: string | null;
    until: string | null;
    activate: boolean;
  }[];
  credits: {
    name: string;
    amount: number;
    period: "month" | "quarter" | "half" | "year";
    note: string;
    per_year: number;
  }[];
  credits_per_year: number;
  perks: string[];
  pairs: string | null;
  choice: string | null;
  source: string;
  verified: boolean;
  checked: string;
};

export type Statement = {
  purchase_apr: number | null;
  aprs: { type: string; percent: number }[];
  statement_cents: number | null;
  statement_date: string | null;
  minimum_cents: number | null;
  due_date: string | null;
  last_payment_cents: number | null;
  last_payment_date: string | null;
  overdue: boolean | null;
  as_of: string | null;
};

export type Role = "active" | "drawer" | "closed";

export type CardRow = {
  account_id: number;
  name: string;
  institution: string;
  mask: string | null;
  label: string;
  linked: boolean;
  suggested: string | null;
  product: ProductDetail | null;
  role: Role | null;
  renewal_month: number | null;
  balance_cents: number | null;
  balance_as_of: string | null;
  limit_cents: number | null;
  utilization: number | null;
  statement: Statement | null;
  spent_month_cents: number;
  earned_year_cents: number;
  caps: {
    what: string;
    used_cents: number;
    cap_cents: number;
    period: string;
  }[];
};

export type Program = {
  id: string;
  name: string;
  unit: string;
  cpp: number;
  default: number;
  transfers: boolean;
};

export type BestRow = {
  category: string;
  label: string;
  spent_cents: number;
  best: Ranked | null;
  next: Ranked | null;
  /** The cards that earn the most here, when more than one does. */
  tied: { account_id: number; card: string }[];
};

/** Which card wins a tie: your default, and your pick per category. */
export type Prefer = { default: number | null; by: Record<string, number> };

/** What a point is worth on one card. Two cards on one program can differ (a Freedom card and a Sapphire card). */
export type CardValue = {
  account_id: number;
  card: string;
  program: string;
  program_name: string;
  cpp: number;
  default: number;
  own: boolean;
  pairs: string | null;
  transfers: boolean;
};

export type CardsPage = {
  demo: boolean;
  as_of: string;
  cards: CardRow[];
  best: BestRow[];
  prefer: Prefer;
  programs: Program[];
  card_values: CardValue[];
  catalog: Product[];
  checked: string;
  offers: Offers;
};

export type Lookup = {
  query: string;
  found: boolean;
  name?: string;
  matches: {
    id: string;
    name: string;
    source: "known" | "history";
    count?: number;
  }[];
  chosen?: string | null;
  kind?: string[];
  how?: string;
  website?: string | null;
  usual?: string;
  channels?: { channel: "in store" | "online"; cards: Ranked[] }[];
  same?: boolean;
  kinds: { id: string; label: string }[];
  /** Card offers at this store, on any of your cards. */
  offers?: (Omit<RankedOffer, "value_cents"> & { card: string })[];
};

export type Missed = {
  months: number;
  start: string;
  end: string;
  spent_cents: number;
  earned_cents: number;
  best_cents: number;
  missed_cents: number;
  categories: {
    category: string;
    label: string;
    spent_cents: number;
    earned_cents: number;
    best_cents: number;
    missed_cents: number;
    use: string | null;
  }[];
  purchases: {
    id: string;
    date: string;
    merchant: string;
    cents: number;
    used: string;
    earned_cents: number;
    best: string;
    best_cents: number;
    missed_cents: number;
  }[];
};

export const PERIOD = {
  month: "a month",
  quarter: "a quarter",
  half: "every 6 months",
  year: "a year",
} as const;
export const ROLES: [Role, string][] = [
  ["active", "Using it"],
  ["drawer", "In a drawer"],
  ["closed", "Closed"],
];
export const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

export const shortDay = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});
export const longDay = new Intl.DateTimeFormat("en-US", {
  month: "long",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

/** "12 days" until a date, from the page's as-of day. */
export function daysUntil(day: string, from: string): number {
  return Math.round((Date.parse(day) - Date.parse(from)) / 86_400_000);
}
