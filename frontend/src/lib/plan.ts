// The Plan page's numbers. The arithmetic is the same as backend/financeapp/plan.py, so the what-if sliders
// answer instantly without asking the server, and agree with what Home and the morning brief say.

export type Level = "lean" | "regular" | "fat";
export type Band = { year: number; low: number; mid: number; high: number };
export type Returns = { low: number; mid: number; high: number };

export type PlanInputs = {
  start_cents: number;
  year: number;
  monthly_savings_cents: number | null;
  measured_savings_cents: number | null;
  // Where the app's own number came from: true savings rate months, pay stubs, take-home deposits, the last 90 days.
  savings_source: { kind: "savings_rate" | "pay_stubs" | "take_home" | "recent" | "none"; note: string };
  savings_typed: boolean;
  returns: Returns;
  returns_source: { typed: boolean; derived_mid: number; note: string; mix: Record<"stocks" | "bonds" | "cash" | "other", number> | null };
  withdrawal_rate: number;
  level: Level;
  birth_year: number | null;
  levels: Record<Level, { yearly_cents: number; typed: boolean; derived_cents: number; note: string }>;
  months_used: number;
  emergency: {
    months: number;
    monthly_cents: number;
    target_cents: number;
    have_cents: number;
  };
};

export type Subscription = {
  merchant: string;
  category: string | null;
  monthly_cents: number;
  yearly_cents: number;
  last_date: string;
  months: number;
  went_up: boolean;
};
export type Rising = {
  category_id: number;
  category: string;
  month: string;
  spent_cents: number;
  usual_cents: number;
  change_cents: number;
};

export type PlanState =
  | { demo: boolean; linked: false }
  | {
      demo: boolean;
      linked: true;
      net_worth_cents: number;
      history: { date: string; cents: number }[];
      inputs: PlanInputs;
      projection: {
        monthly_savings_cents: number;
        bands: Band[];
        target_cents: number | null;
        fire_year: Returns | null;
      } | null;
      ideas: {
        subscriptions: Subscription[];
        subscriptions_yearly_cents: number;
        rising: Rising[];
      };
    };

export const YEARS = 30;
export const SPREAD = 2;
export const MAX_YEARS = 60;
/** A few words on where the app's monthly savings number came from, for a caption. */
export const SAVINGS_SHORT: Record<PlanInputs["savings_source"]["kind"], string> = {
  savings_rate: "from your pay stubs and spending",
  pay_stubs: "from your pay stubs",
  take_home: "take-home pay minus spending",
  recent: "the last 90 days",
  none: "",
};

export const LEVEL_NAMES: Record<Level, { title: string; body: string }> = {
  lean: { title: "Lean", body: "Bills and essentials only" },
  regular: { title: "Regular", body: "Life as you spend now" },
  fat: { title: "Comfortable", body: "Half again on top" },
};

/** Where `start` gets to after `months`, adding `monthly` at the end of each month, at a yearly return. */
export function grow(start: number, monthly: number, ratePercent: number, months: number): number {
  const r = ratePercent / 100 / 12;
  if (r === 0) return Math.round(start + monthly * months);
  const f = (1 + r) ** months;
  return Math.round(start * f + (monthly * (f - 1)) / r);
}

/** Months until the target is reached (0 if it already is), or null if not within 60 years. */
export function monthsTo(target: number, start: number, monthly: number, ratePercent: number): number | null {
  if (start >= target) return 0;
  let value = start;
  const r = ratePercent / 100 / 12;
  for (let m = 1; m <= MAX_YEARS * 12; m++) {
    value = value * (1 + r) + monthly;
    if (value >= target) return m;
  }
  return null;
}

export function targetFor(yearlySpending: number, withdrawalRate: number): number {
  return Math.round(yearlySpending / (withdrawalRate / 100));
}

export type Scenario = {
  monthly: number;
  ret: number;
  yearly: number;
  withdrawal: number;
};

/** The bands and the year the target is reached, for one set of slider values. `ret` is the middle return. */
export function project(i: PlanInputs, s: Scenario) {
  // The range stays two points either side of the middle, as on the server.
  const returns = { low: Math.max(s.ret - SPREAD, 0), mid: s.ret, high: s.ret + SPREAD };
  const bands: Band[] = [];
  for (let y = 0; y <= YEARS; y++) {
    const m = y * 12;
    bands.push({
      year: i.year + y,
      low: grow(i.start_cents, s.monthly, returns.low, m),
      mid: grow(i.start_cents, s.monthly, returns.mid, m),
      high: grow(i.start_cents, s.monthly, returns.high, m),
    });
  }
  const target = s.yearly > 0 ? targetFor(s.yearly, s.withdrawal) : null;
  const reached = (rate: number) => {
    if (target === null) return null;
    const m = monthsTo(target, i.start_cents, s.monthly, rate);
    return m === null ? null : Math.floor((i.year * 12 + m) / 12);
  };
  return {
    bands,
    returns,
    target,
    fire: {
      low: reached(returns.low),
      mid: reached(returns.mid),
      high: reached(returns.high),
    },
  };
}
