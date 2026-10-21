"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { resetHousehold } from "@/lib/household";

export type BudgetLine = {
  category_id: number;
  category: string;
  kind: "fixed" | "flexible";
  essential: boolean;
  limit_cents: number;
  spent_cents: number;
  typical_cents: number;
  /** Where this category is heading by month-end; null while it's too early to say. */
  forecast?: Forecast | null;
};
export type Forecast = {
  projected_cents: number;
  over_cents: number;
  status: "on_track" | "close" | "heading_over" | "over";
  /** Once-a-month charges that haven't come yet. */
  due_cents: number;
  due: { merchant: string; cents: number; day: number }[];
  /** What the everyday pace adds for the days still to come. */
  pace_cents: number;
  days_left: number;
};
export type SavingsMonth = {
  month: string;
  percent: number;
  basis: "gross" | "take_home";
  income_cents: number;
  taxes_cents: number;
  deductions_cents: number;
  spending_cents: number;
  saved_cents: number;
  match_cents: number;
  stubs: number;
  /** The month line by line: money in, money out (spending by group), where the savings went, what to check. */
  breakdown?: SavingsBreakdown;
};
export type SavingsLine = { label: string; cents: number; items?: { label: string; cents: number }[] };
export type SavingsBreakdown = {
  money_in: SavingsLine[];
  money_out: SavingsLine[];
  went: (SavingsLine & { kind: "retirement" | "savings" | "invest" | "loan" | "card" | "other" | "moved_in" | "left" })[];
  notes: { kind: "take_home" | "unlinked_cards" | "other_transfers" | "other_income"; cents?: number }[];
};
export type Version = { id: number; source: "draft" | "edit" | "restore" | "ai"; by: string | null; at: string; total_cents: number };
export type Frequency = "weekly" | "biweekly" | "semimonthly" | "monthly";
export type Schedule = {
  user_id: number | null;
  person: string | null;
  frequency: Frequency;
  next_date: string;
  monthly_gross_cents: number;
  monthly_net_cents: number;
};
export type Paycheck = {
  id: number;
  user_id: number | null;
  person: string | null;
  date: string;
  gross_cents: number;
  taxes_cents: number;
  savings_cents: number;
  net_cents: number;
  employer_match_cents: number;
  frequency: Frequency | null;
  deposit: { date: string; merchant: string } | null;
};
export type BudgetState = {
  saved: boolean;
  version: Version | null;
  months_used: number;
  rough: boolean;
  month: string;
  lines: BudgetLine[];
  unbudgeted: { category_id: number | null; category: string; kind: string; spent_cents: number }[];
  safe_to_spend: { cents: number; days_left: number } | null;
  forecast: { projected_cents: number; limit_cents: number; heading_over: number[]; days_left: number } | null;
  savings_rate: SavingsMonth | null;
  savings_history: SavingsMonth[];
  emergency: { months: number; monthly_cents: number; target_cents: number; have_cents: number };
  include_match: boolean;
  versions: Version[];
  paychecks: Paycheck[];
  people: { id: number; name: string }[];
  schedules: Schedule[];
  categories: { id: number; name: string; kind: "fixed" | "flexible"; essential: boolean }[];
};

export const monthName = new Intl.DateTimeFormat("en-US", { month: "long", timeZone: "UTC" });

/**
 * The Budget page's data. Every change returns the whole budget again, so the page updates in place; Home's
 * numbers (safe to spend, closest to their limit) are refreshed in the background.
 */
export function useBudget() {
  const [data, setData] = useState<BudgetState | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api<BudgetState>("/api/budget").then(
      (d) => live && setData(d),
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
    };
  }, []);
  const change = useCallback(async (path: string, method: string, body?: unknown) => {
    const d = await api<BudgetState>(path, { method, body });
    setData(d);
    resetHousehold();
    return d;
  }, []);
  return { data, error, change };
}

/** Every line as the server wants it on a save, with one changed (a limit of 0 takes it out of the budget). */
export function linesWith(lines: BudgetLine[], categoryId: number, limitCents: number) {
  const rest = lines.filter((l) => l.category_id !== categoryId).map((l) => ({ category_id: l.category_id, limit_cents: l.limit_cents }));
  return limitCents > 0 ? [...rest, { category_id: categoryId, limit_cents: limitCents }] : rest;
}
