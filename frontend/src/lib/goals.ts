"use client";

import { Baby, Car, GraduationCap, Gift, Heart, House, Laptop, PiggyBank, Plane, Gem, ShieldCheck, Star } from "lucide-react";

export type Funding = { account_id: number; account: string; cents: number | null; counted_cents: number; short: boolean };
export type GoalStatus = "done" | "on_track" | "behind" | "open" | "no_target";
export type Goal = {
  id: number;
  kind: "emergency" | "saving";
  name: string;
  icon: IconName;
  color: ColorName;
  priority: 1 | 2 | 3;
  target_cents: number;
  target_typed: boolean;
  target_date: string | null;
  rate_percent: number;
  rate_typed: boolean;
  monthly_typed_cents: number | null;
  saved_cents: number;
  percent: number | null;
  months_left: number | null;
  needed_cents: number | null;
  /** The emergency fund with no accounts picked: it counts the cash no other goal has. */
  auto: boolean;
  funding: Funding[];
  monthly_cents: number | null;
  monthly_source: "typed" | "savings" | "needed" | "done" | null;
  projected_month: string | null;
  status: GoalStatus;
};
export type GoalAccount = {
  id: number;
  name: string;
  type: string;
  balance_cents: number;
  free_cents: number;
  rest_goal_id: number | null;
};
export type GoalsState = {
  demo: boolean;
  goals: Goal[];
  accounts: GoalAccount[];
  summary: {
    monthly_savings_cents: number | null;
    needed_cents: number;
    planned_cents: number;
    set_aside_cents: number;
    short_cents: number | null;
  };
  emergency: { months: number; monthly_essentials_cents: number };
  suggestions: { name: string; icon: IconName; color: ColorName }[];
  default_rate: number;
  month: string;
};

export type GoalIn = {
  name: string;
  icon: IconName;
  color: ColorName;
  priority: 1 | 2 | 3;
  target_cents: number | null;
  target_date: string | null;
  rate_percent: number | null;
  monthly_cents: number | null;
  funding: { account_id: number; cents: number | null }[];
  emergency_months?: number;
};

export const ICONS = {
  house: House,
  car: Car,
  plane: Plane,
  ring: Gem,
  school: GraduationCap,
  baby: Baby,
  heart: Heart,
  gift: Gift,
  laptop: Laptop,
  piggy: PiggyBank,
  shield: ShieldCheck,
  star: Star,
} as const;
export type IconName = keyof typeof ICONS;

/** Each goal's color: a soft tile behind its icon, and its progress bar. */
export const COLORS = {
  teal: { tile: "bg-chart-1/15 text-chart-1", bar: "bg-chart-1", dot: "bg-chart-1" },
  blue: { tile: "bg-chart-2/15 text-chart-2", bar: "bg-chart-2", dot: "bg-chart-2" },
  amber: { tile: "bg-chart-3/20 text-warning", bar: "bg-chart-3", dot: "bg-chart-3" },
  pink: { tile: "bg-chart-4/15 text-chart-4", bar: "bg-chart-4", dot: "bg-chart-4" },
  slate: { tile: "bg-chart-5/20 text-muted-foreground", bar: "bg-chart-5", dot: "bg-chart-5" },
} as const;
export type ColorName = keyof typeof COLORS;

export const PRIORITY = { 1: "High", 2: "Medium", 3: "Low" } as const;

export const monthYear = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
export const monthLong = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });

// ---- the arithmetic (backend/financeapp/goals.py does the same, so the what-if slider answers instantly)

/** Months until `start` reaches `target`, adding `monthly` at the end of each month, or null past 60 years. */
export function monthsTo(target: number, start: number, monthly: number, ratePercent: number): number | null {
  if (start >= target) return 0;
  const r = ratePercent / 100 / 12;
  let value = start;
  for (let m = 1; m <= 60 * 12; m++) {
    value = value * (1 + r) + monthly;
    if (value >= target) return m;
  }
  return null;
}

/** What has to go in each month to get from `start` to `target` in `months`. */
export function neededMonthly(target: number, start: number, months: number, ratePercent: number): number {
  if (start >= target) return 0;
  if (months <= 0) return target - start;
  const r = ratePercent / 100 / 12;
  if (r === 0) return Math.ceil((target - start) / months);
  const f = (1 + r) ** months;
  return Math.max(Math.ceil(((target - start * f) * r) / (f - 1)), 0);
}

export function addMonths(isoMonth: string, n: number): Date {
  const d = new Date(isoMonth);
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + n, 1));
}

export function monthsBetween(isoMonth: string, isoDate: string): number {
  const a = new Date(isoMonth);
  const b = new Date(isoDate);
  return (b.getUTCFullYear() - a.getUTCFullYear()) * 12 + b.getUTCMonth() - a.getUTCMonth();
}
