/** The Income over time page's numbers, as GET /api/earnings sends them (backend/financeapp/earnings.py). */

import type { AIOffer } from "@/components/ai-offer";

export type SourceKey = "base" | "bonus" | "rsu" | "other" | "match" | "employer_hsa";
export type How = "" | "ytd" | "stubs" | "estimated" | "take_home" | "typed" | "w2" | "stub";

export type EarningsSource = { key: SourceKey; label: string; cents: number; full_cents: number; how: How };

export type Raise = {
  percent: number;
  from_cents: number;
  inflation: { percent: number; latest: boolean; through: string | null } | null;
  real: number | null;
  verdict: "ahead" | "behind" | "even" | null;
};

export type EarningsYear = {
  year: number;
  current: boolean;
  complete: boolean;
  through: string | null;
  take_home: boolean;
  stubs: number;
  estimated_paychecks: number;
  bonus_guessed: boolean;
  typed: SourceKey[];
  source: "typed" | "w2" | "stub" | null;
  note: string;
  events: { id: string; effective: string; text: string; kind: string | null }[];
  sources: EarningsSource[];
  pay_cents: number;
  comp_cents: number;
  full_pay_cents: number;
  full_comp_cents: number;
  projected: { paychecks_left: number; per_paycheck_cents: number; raise_from: string | null } | null;
  rate_cents: number | null;
  rate_how: "stub" | "letter" | "total" | "";
  growth: { pay: number | null; base: number | null; comp: number | null; on_track: boolean } | null;
  raise: Raise | null;
};

export type Letter = {
  id: string;
  kind: "raise" | "promotion" | "bonus" | "offer";
  effective: string;
  title?: string;
  base_cents?: number;
  previous_base_cents?: number;
  bonus_target_percent?: number;
  bonus_target_cents?: number;
  bonus_cents?: number;
  stock_cents?: number;
};

export type EarningsState = {
  demo: boolean;
  year: number;
  years: EarningsYear[];
  now: {
    base_cents: number | null;
    base_how: string;
    bonus_target_percent: number | null;
    bonus_target_cents: number | null;
    cash_cents: number | null;
    coming: { effective: string; base_cents: number } | null;
    keeps_share: number | null;
  } | null;
  letters: Letter[];
  has_stubs: boolean;
  linked_from: string | null;
  labels: Record<SourceKey, string>;
  inflation: { sources: { title: string; url: string }[]; note: string };
};

/** What reading a letter gives back, for the form. */
export type LetterRead = {
  kind: Letter["kind"];
  effective: string | null;
  title: string;
  base_cents: number | null;
  previous_base_cents: number | null;
  bonus_target_percent: number | null;
  bonus_target_cents: number | null;
  bonus_cents: number | null;
  stock_cents: number | null;
  warnings: string[];
  ai_cost?: number;
};

/** What reading a W-2 or a year's last stub gives back, for the year form. */
export type YearRead = {
  year: number | null;
  source: "w2" | "stub";
  base_cents: number;
  bonus_cents: number | null;
  rsu_cents: number | null;
  match_cents: number | null;
  employer_hsa_cents: number | null;
  warnings: string[];
  ai_cost?: number;
};

export type ReadAnswer<T> = T | { ai_offer: AIOffer };

/** The chart's series, in a fixed order: each source keeps its color whatever's shown. */
export const SERIES: { keys: SourceKey[]; label: string; color: string }[] = [
  { keys: ["base"], label: "Base", color: "var(--chart-1)" },
  { keys: ["bonus"], label: "Bonus", color: "var(--chart-2)" },
  { keys: ["rsu"], label: "Stock", color: "var(--chart-3)" },
  { keys: ["other"], label: "Other", color: "var(--chart-5)" },
  { keys: ["match", "employer_hsa"], label: "From your employer", color: "var(--chart-4)" },
];

export const HOW: Record<How, string> = {
  "": "",
  ytd: "year to date on the last stub",
  stubs: "from your pay stubs",
  estimated: "partly estimated from paychecks without a stub",
  take_home: "take-home only (no pay stub yet)",
  typed: "typed in",
  w2: "from a W-2",
  stub: "from a pay stub",
};

export function pct(v: number, signed = true) {
  return `${signed && v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}%`;
}
