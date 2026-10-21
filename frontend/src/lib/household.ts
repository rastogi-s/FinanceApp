"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

export type Budget = { category: string; category_id?: number; kind: "fixed" | "flexible"; limit_cents: number; spent_cents: number };
export type Transaction = {
  id: string;
  date: string;
  merchant: string;
  category: string;
  category_id?: number | null;
  amount_cents: number;
  account: string;
  transfer?: boolean;
  pending?: boolean;
  needs_review?: boolean;
  by_hand?: boolean;
};
export type Holding = {
  symbol: string;
  name: string;
  asset_class: string;
  account: string;
  value_cents: number;
  change_today_percent: number;
  account_id?: number;
  quantity?: number;
  price?: number;
  cost_basis_cents?: number | null;
  live?: boolean;
};
export type Alert = {
  id: string;
  kind: "budget" | "price" | "concentration";
  title: string;
  body: string;
  at: string;
  status?: "pending" | "held" | "sent" | "unsent" | "failed";
  channel?: string | null;
  read?: boolean;
};
export type AccountRow = { id?: number; name: string; type?: string; cents: number; as_of?: string | null };

export type LinkedHousehold = {
  demo: boolean;
  linked: true;
  as_of: string;
  net_worth: { cents: number; change_today_cents: number; history: { date: string; cents: number }[] };
  spending?: { month_cents: number; last_month_same_day_cents: number; needs_review: number };
  // Null (or empty) until there's a budget, income, or (for projections) a later phase.
  safe_to_spend: { cents: number; days_left: number } | null;
  savings_rate: { percent: number; month: string; basis: "gross" | "take_home" } | null;
  budgets: Budget[];
  budgets_closest: Budget[];
  alerts: Alert[];
  transactions: Transaction[];
  accounts: { cash: AccountRow[]; investments?: AccountRow[]; debts: AccountRow[] };
  holdings: Holding[];
  prices_as_of?: string | null;
  allocation: { asset_class: string; cents: number }[];
  projection: { monthly_savings_cents: number; bands: { year: number; low: number; mid: number; high: number }[] } | null;
  health?: { stale: { id: number; institution: string; error: string | null }[]; last_sync_ok: boolean | null };
};

export type Household = LinkedHousehold | { demo: false; linked: false };

export type State = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; household: Household };

// Fetched once and shared by every page, so moving between tabs is instant. Cleared on sign-out and after changes,
// and fetched again once it is a few minutes old, so a background sync shows up without reopening the app.
const STALE_MS = 5 * 60_000;
let cached: Promise<Household> | null = null;
let cachedAt = 0;
const listeners = new Set<() => void>();

function stale(): boolean {
  return cached !== null && Date.now() - cachedAt > STALE_MS;
}

function load(): Promise<Household> {
  if (stale()) cached = null;
  if (!cached) {
    cached = api<Household>("/api/household");
    cachedAt = Date.now();
    cached.catch(() => {
      cached = null; // try again next time
    });
  }
  return cached;
}

// Back to the app after a while (a home-screen app is often resumed, not reopened): show fresh numbers.
function refreshIfStale() {
  if (document.visibilityState === "visible" && stale()) resetHousehold(true);
}

/** Forget the household so the next page shows fresh numbers (after a sync, an import or a recategorize). */
export function resetHousehold(reload = false) {
  cached = null;
  if (reload) listeners.forEach((l) => l());
}

/** Pull to refresh: sync with the banks, then fresh numbers on every page, including the ones that fetch their own. */
export async function refreshAll(): Promise<void> {
  // Sync problems show on Settings → Accounts; a sync already running or sample data still gets fresh numbers.
  const shown = await (cached ?? Promise.reject()).catch(() => null);
  if (!shown?.demo) await api("/api/sync", { method: "POST" }).catch(() => {});
  resetHousehold(true);
  window.dispatchEvent(new Event("app-refresh"));
  await load().catch(() => {});
}

export function useHousehold(): State {
  const [state, setState] = useState<State>({ status: "loading" });
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const l = () => setVersion((v) => v + 1);
    listeners.add(l);
    document.addEventListener("visibilitychange", refreshIfStale);
    return () => {
      listeners.delete(l);
      document.removeEventListener("visibilitychange", refreshIfStale);
    };
  }, []);
  useEffect(() => {
    let live = true;
    load().then(
      (household) => live && setState({ status: "ready", household }),
      (e: Error) => live && setState({ status: "error", message: e.message }),
    );
    return () => {
      live = false;
    };
  }, [version]);
  return state;
}

/** GET a JSON endpoint, with a reload function. */
export function useApi<T>(path: string | null): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    if (!path) return;
    let live = true;
    api<T>(path).then(
      (d) => {
        if (!live) return;
        setData(d);
        setError(null);
      },
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
    };
  }, [path, version]);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  useEffect(() => {
    window.addEventListener("app-refresh", reload);
    return () => window.removeEventListener("app-refresh", reload);
  }, [reload]);
  return { data, error, reload };
}

export type Meta = { version: string; demo: boolean };

export function useMeta(): Meta | null {
  const { data } = useApi<Meta>("/api/meta");
  return data;
}
