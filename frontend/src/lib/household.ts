"use client";

import { useEffect, useState } from "react";

export type Budget = { category: string; kind: "fixed" | "flexible"; limit_cents: number; spent_cents: number };
export type Transaction = {
  id: string;
  date: string;
  merchant: string;
  category: string;
  amount_cents: number;
  account: string;
  transfer?: boolean;
};
export type Holding = {
  symbol: string;
  name: string;
  asset_class: string;
  account: string;
  value_cents: number;
  change_today_percent: number;
};
export type Alert = { id: string; kind: "budget" | "price"; title: string; body: string; at: string };

export type LinkedHousehold = {
  demo: boolean;
  linked: true;
  as_of: string;
  net_worth: { cents: number; change_today_cents: number; history: { date: string; cents: number }[] };
  safe_to_spend: { cents: number; days_left: number };
  savings_rate: { percent: number; month: string };
  budgets: Budget[];
  budgets_closest: Budget[];
  alerts: Alert[];
  transactions: Transaction[];
  accounts: { cash: { name: string; cents: number }[]; debts: { name: string; cents: number }[] };
  holdings: Holding[];
  allocation: { asset_class: string; cents: number }[];
  projection: { monthly_savings_cents: number; bands: { year: number; low: number; mid: number; high: number }[] };
};

export type Household = LinkedHousehold | { demo: false; linked: false };

export type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; household: Household };

// Fetched once per visit and shared by every page, so moving between tabs is instant.
let cached: Promise<Household> | null = null;

function load(): Promise<Household> {
  cached ??= fetch("/api/household", { credentials: "same-origin" }).then((r) => {
    if (!r.ok) throw new Error(`The server answered ${r.status}`);
    return r.json() as Promise<Household>;
  });
  cached.catch(() => {
    cached = null; // try again next time
  });
  return cached;
}

export function useHousehold(): State {
  const [state, setState] = useState<State>({ status: "loading" });
  useEffect(() => {
    let live = true;
    load().then(
      (household) => live && setState({ status: "ready", household }),
      (e: Error) => live && setState({ status: "error", message: e.message }),
    );
    return () => {
      live = false;
    };
  }, []);
  return state;
}

export type Meta = { version: string; demo: boolean };

export function useMeta(): Meta | null {
  const [meta, setMeta] = useState<Meta | null>(null);
  useEffect(() => {
    fetch("/api/meta")
      .then((r) => (r.ok ? r.json() : null))
      .then(setMeta, () => setMeta(null));
  }, []);
  return meta;
}
