"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowDownRight, ArrowUpRight, ChevronDown, ChevronRight, Info, Layers, RefreshCw, Search, X } from "lucide-react";
import { ExposureGraph } from "@/components/exposure-graph";
import { Money } from "@/components/money";
import { HouseholdPage } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { type Holding, type LinkedHousehold, resetHousehold, useApi } from "@/lib/household";
import { type Company, type Portfolio, asOf, hasDetail, holdingHref, percent } from "@/lib/portfolio";
import { cn } from "@/lib/utils";

const COLORS = ["bg-chart-1", "bg-chart-2", "bg-chart-3", "bg-chart-4", "bg-chart-5"];
const time = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });
const day = new Intl.DateTimeFormat("en-US", { weekday: "short", hour: "numeric", minute: "2-digit" });

export default function PortfolioPage() {
  return (
    <HouseholdPage title="Portfolio" subtitle="Every account combined, at live prices.">
      {(h) => <Body h={h} />}
    </HouseholdPage>
  );
}

function Body({ h }: { h: LinkedHousehold }) {
  const { data: p, reload } = useApi<Portfolio>("/api/portfolio");
  const { state } = useAuth();
  const demo = state.status === "signed-in" && state.demo;
  const [refreshing, setRefreshing] = useState(false);

  const refresh = async () => {
    if (demo) return toast("Prices don't change while the app shows sample data.");
    setRefreshing(true);
    try {
      const r = await api<{ quotes: number }>("/api/portfolio/refresh", { method: "POST" });
      toast(r.quotes ? "Prices updated." : "No new prices right now.");
      resetHousehold(true);
      reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setRefreshing(false);
    }
  };

  const invested = h.holdings.reduce((s, x) => s + x.value_cents, 0);
  const today = h.holdings.reduce((s, x) => s + (x.value_cents - x.value_cents / (1 + x.change_today_percent / 100)), 0);
  const pricesAt = p?.prices_as_of ?? h.prices_as_of;
  const pricesText = pricesAt
    ? `Prices ${new Date(pricesAt).toDateString() === new Date().toDateString() ? time.format(new Date(pricesAt)) : day.format(new Date(pricesAt))}`
    : "Prices from the last sync";

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <Card className="md:col-span-2">
        <CardHeader>
          <CardTitle>Invested</CardTitle>
          <Button variant="ghost" size="sm" onClick={refresh} disabled={refreshing} aria-label="Update prices now">
            <RefreshCw className={cn(refreshing && "animate-spin motion-reduce:animate-none")} /> {pricesText}
          </Button>
        </CardHeader>
        <CardContent>
          <p className="text-4xl font-semibold tracking-tight">
            <Money cents={invested} />
          </p>
          {h.holdings.length > 0 && (
            <p className={cn("mt-1 flex items-center gap-1 text-sm font-medium", today >= 0 ? "text-positive" : "text-negative")}>
              {today >= 0 ? <ArrowUpRight className="size-4" aria-hidden /> : <ArrowDownRight className="size-4" aria-hidden />}
              <Money cents={Math.round(today)} signed /> today
              {p && <span className="ml-1 font-normal text-muted-foreground">· markets {p.market_open ? "open" : "closed"}</span>}
            </p>
          )}
        </CardContent>
      </Card>

      <Allocation h={h} />
      {p ? <Largest p={p} /> : <Skeleton className="h-72 rounded-xl" />}
      {p ? <RealExposure p={p} /> : <Skeleton className="h-72 rounded-xl md:col-span-2" />}
      {p && p.exposure.companies.length > 0 && (
        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle>How it connects</CardTitle>
            <Layers className="size-4 text-muted-foreground" aria-hidden />
          </CardHeader>
          <CardContent>
            <ExposureGraph exposure={p.exposure} />
          </CardContent>
        </Card>
      )}
      <Holdings holdings={h.holdings} />
    </div>
  );
}

function Allocation({ h }: { h: LinkedHousehold }) {
  const total = h.allocation.reduce((s, a) => s + a.cents, 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Allocation</CardTitle>
        <span className="text-sm font-medium">
          <Money cents={total} />
        </span>
      </CardHeader>
      <CardContent>
        <div
          className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full"
          role="img"
          aria-label={h.allocation.map((a) => `${a.asset_class} ${Math.round((a.cents / total) * 100)}%`).join(", ")}
        >
          {h.allocation.map((a, i) => (
            <div key={a.asset_class} className={COLORS[i % COLORS.length]} style={{ width: `${(a.cents / total) * 100}%` }} />
          ))}
        </div>
        <ul className="mt-4 grid gap-2.5">
          {h.allocation.map((a, i) => (
            <li key={a.asset_class} className="flex items-center gap-2.5 text-sm">
              <span className={cn("size-2.5 rounded-full", COLORS[i % COLORS.length])} aria-hidden />
              <span className="flex-1">{a.asset_class}</span>
              <Money cents={a.cents} className="text-muted-foreground" />
              <span className="w-14 text-right tabular-nums">{((a.cents / total) * 100).toFixed(1)}%</span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-muted-foreground">Includes cash in the bank.</p>
      </CardContent>
    </Card>
  );
}

function Largest({ p }: { p: Portfolio }) {
  const top = p.positions.slice(0, 6);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Largest positions</CardTitle>
        {p.positions.length > 5 && <Badge variant={p.top5_percent >= 60 ? "warning" : "muted"}>Top 5: {percent(p.top5_percent, 0)}</Badge>}
      </CardHeader>
      <CardContent className="pt-1">
        {top.length === 0 && <p className="text-sm text-muted-foreground">No holdings yet.</p>}
        <ul className="grid grid-cols-1 gap-1">
          {top.map((x) => {
            const body = (
              <>
                <div className="mb-1.5 flex items-baseline justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate">
                    <span className="font-semibold">{x.symbol}</span>
                    <span className="text-muted-foreground"> · {x.accounts.length > 1 ? `${x.accounts.length} accounts` : x.accounts[0]?.account}</span>
                  </span>
                  <span className="shrink-0 tabular-nums">{percent(x.percent)}</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
                  <div className="h-full rounded-full bg-chart-1" style={{ width: `${Math.min(x.percent / (top[0]?.percent || 1), 1) * 100}%` }} />
                </div>
              </>
            );
            return (
              <li key={x.symbol}>
                {hasDetail(x.symbol, x.asset_class) ? (
                  <Link href={holdingHref(x.symbol)} className="-mx-2 block rounded-lg px-2 py-2 hover:bg-muted/50">
                    {body}
                  </Link>
                ) : (
                  <div className="py-2">{body}</div>
                )}
              </li>
            );
          })}
        </ul>
        <p className="mt-2 text-xs text-muted-foreground">Share of everything invested. The same fund in two accounts counts once.</p>
      </CardContent>
    </Card>
  );
}

function RealExposure({ p }: { p: Portfolio }) {
  const [all, setAll] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const e = p.exposure;
  const ranked = useMemo(() => e.companies.map((c, i) => ({ c, rank: i + 1 })), [e.companies]);
  const needle = q.trim().toLowerCase();
  const found = needle
    ? ranked.filter(({ c }) => c.symbol?.toLowerCase().startsWith(needle) || c.name.toLowerCase().includes(needle))
    : null;
  const shown = found ? found.slice(0, 50) : ranked.slice(0, all ? 50 : 10);
  const pending = e.funds.filter((f) => f.status !== "ok");
  const dates = e.funds.filter((f) => f.as_of).map((f) => f.as_of!);
  const newest = dates.length ? dates.reduce((a, b) => (a > b ? a : b)) : null;

  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>Your real exposure</CardTitle>
        <Layers className="size-4 text-muted-foreground sm:hidden" aria-hidden />
        <span className="hidden text-xs text-muted-foreground sm:inline">Counting what&apos;s inside your funds</span>
      </CardHeader>
      <CardContent className="pt-1">
        {e.companies.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {!p.sources.lookthrough
              ? "Looking inside funds is turned off on the server."
              : e.funds.length
                ? "Looking up what your funds hold at the SEC. It can take a few minutes the first time."
                : "Hold a stock or a fund and this shows your true share of every company."}
          </p>
        ) : (
          <>
            {e.companies.length > 10 && (
              <SearchBox
                value={q}
                onChange={setQ}
                label="Find a company"
                placeholder={`Find any of ${e.companies.length.toLocaleString("en-US")} companies`}
                className="mb-2"
              />
            )}
            {found && found.length === 0 && (
              <p className="py-4 text-sm text-muted-foreground">You don&apos;t own any of that, directly or through your funds.</p>
            )}
            <ol className="divide-y">
              {shown.map(({ c, rank }) => (
                <ExposureRow key={c.key} c={c} rank={rank} open={open === c.key} toggle={() => setOpen(open === c.key ? null : c.key)} />
              ))}
            </ol>
          </>
        )}
        {e.companies.length > 10 && !found && (
          <Button variant="ghost" size="sm" className="mt-2" onClick={() => setAll(!all)}>
            {all ? "Top 10 only" : `Show top ${Math.min(50, e.companies.length)}`}
          </Button>
        )}
        <div className="mt-3 flex gap-2 rounded-xl bg-muted/50 p-3 text-xs text-muted-foreground">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <p>
            Looks inside {percent(e.looked_through_percent, 0)} of what you hold, from each fund&apos;s latest SEC filing
            {e.oldest_as_of && newest && (e.oldest_as_of === newest ? ` (as of ${asOf(newest)})` : ` (as of ${asOf(e.oldest_as_of)} to ${asOf(newest)})`)}.
            {pending.length > 0 && ` Not yet: ${pending.map((f) => f.symbol).join(", ")}.`}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function ExposureRow({ c, rank, open, toggle }: { c: Company; rank: number; open: boolean; toggle: () => void }) {
  const detail = hasDetail(c.symbol);
  return (
    <li className="py-1">
      <button className="flex w-full items-center gap-3 py-2 text-left" onClick={toggle} aria-expanded={open}>
        <span className="w-5 text-xs text-muted-foreground tabular-nums">{rank}</span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold">{c.symbol ?? c.name}</span>
          <span className="block truncate text-xs text-muted-foreground">
            {c.symbol ? `${c.name} · ` : ""}
            {c.sources.length === 1
              ? c.sources[0]!.symbol === "direct"
                ? "held directly"
                : `through ${c.sources[0]!.symbol}`
              : `through ${c.sources.length} ${c.sources.some((s) => s.symbol === "direct") ? "holdings" : "funds"}`}
          </span>
        </span>
        <span className="text-right">
          <span className="block text-sm font-medium tabular-nums">{percent(c.percent, 2)}</span>
          <Money cents={c.cents} className="block text-xs text-muted-foreground" />
        </span>
        <ChevronDown className={cn("size-4 text-muted-foreground transition-transform motion-reduce:transition-none", open && "rotate-180")} aria-hidden />
      </button>
      {open && (
        <div className="mb-2 ml-8 rounded-xl bg-muted/50 p-3">
          <ul className="grid grid-cols-1 gap-1.5 text-sm">
            {c.sources.map((s) => (
              <li key={s.symbol} className="flex justify-between gap-3">
                <span>{s.symbol === "direct" ? "Held directly" : `Inside ${s.symbol}`}</span>
                <span className="tabular-nums">
                  {percent(s.percent, 2)} · <Money cents={s.cents} />
                </span>
              </li>
            ))}
          </ul>
          {detail && (
            <Link href={holdingHref(c.symbol!)} className="mt-2 inline-flex items-center text-sm font-medium text-primary">
              {c.symbol} details <ChevronRight className="size-4" aria-hidden />
            </Link>
          )}
        </div>
      )}
    </li>
  );
}

type Sort = "value" | "today" | "gain" | "name";
const SORTS: { id: Sort; label: string }[] = [
  { id: "value", label: "Biggest" },
  { id: "today", label: "Movers" },
  { id: "gain", label: "Gainers" },
  { id: "name", label: "A to Z" },
];

const gain = (x: Holding) => (x.cost_basis_cents ? (x.value_cents - x.cost_basis_cents) / x.cost_basis_cents : null);

function Holdings({ holdings }: { holdings: Holding[] }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("value");
  const [account, setAccount] = useState("");
  const [assetClass, setAssetClass] = useState("");
  const accounts = useMemo(() => [...new Set(holdings.map((x) => x.account))].sort(), [holdings]);
  const classes = useMemo(() => [...new Set(holdings.map((x) => x.asset_class))].sort(), [holdings]);
  const hasGain = holdings.some((x) => x.cost_basis_cents);
  const needle = q.trim().toLowerCase();
  const rows = holdings
    .filter(
      (x) =>
        (!needle || x.symbol.toLowerCase().startsWith(needle) || x.name.toLowerCase().includes(needle)) &&
        (!account || x.account === account) &&
        (!assetClass || x.asset_class === assetClass),
    )
    .sort((a, b) => {
      if (sort === "name") return a.symbol.localeCompare(b.symbol);
      if (sort === "today") return Math.abs(b.change_today_percent) - Math.abs(a.change_today_percent);
      if (sort === "gain") return (gain(b) ?? -Infinity) - (gain(a) ?? -Infinity);
      return b.value_cents - a.value_cents;
    });
  const narrowed = rows.length !== holdings.length;
  const total = rows.reduce((s, x) => s + x.value_cents, 0);

  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>Holdings</CardTitle>
        <span className="text-xs text-muted-foreground" aria-live="polite">
          {narrowed ? (
            <>
              {rows.length} of {holdings.length} · <Money cents={total} />
            </>
          ) : (
            "Tap one for its chart and details"
          )}
        </span>
      </CardHeader>
      {holdings.length > 3 && (
        <CardContent className="grid grid-cols-1 gap-2 pt-1 pb-3 sm:grid-cols-[1fr_auto]">
          <SearchBox value={q} onChange={setQ} label="Search holdings" placeholder="Ticker or name" />
          <div className="grid grid-cols-2 gap-2 sm:flex">
            <Select aria-label="Sort holdings" value={sort} onChange={(e) => setSort(e.target.value as Sort)} className="rounded-full">
              {SORTS.filter((x) => x.id !== "gain" || hasGain).map((x) => (
                <option key={x.id} value={x.id}>
                  {x.label}
                </option>
              ))}
            </Select>
            {accounts.length > 1 && (
              <Select aria-label="Account" value={account} onChange={(e) => setAccount(e.target.value)} className="rounded-full sm:max-w-48">
                <option value="">All accounts</option>
                {accounts.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </Select>
            )}
          </div>
          {classes.length > 1 && (
            <div className="-mx-6 flex gap-2 overflow-x-auto px-6 [scrollbar-width:none] sm:col-span-2" role="group" aria-label="Kind of holding">
              {["", ...classes].map((c) => (
                <button
                  key={c || "all"}
                  type="button"
                  aria-pressed={assetClass === c}
                  onClick={() => setAssetClass(c)}
                  className={cn(
                    "inline-flex h-8 shrink-0 items-center rounded-full border px-3 text-xs font-medium transition-colors",
                    assetClass === c ? "border-transparent bg-foreground text-background" : "bg-card text-muted-foreground hover:text-foreground",
                  )}
                >
                  {c || "Everything"}
                </button>
              ))}
            </div>
          )}
        </CardContent>
      )}
      {holdings.length === 0 && (
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No holdings yet. They appear after the next sync of an investment account, or upload a positions file in Settings → Accounts.
          </p>
        </CardContent>
      )}
      {holdings.length > 0 && rows.length === 0 && (
        <CardContent>
          <p className="text-sm text-muted-foreground">No holdings match that.</p>
        </CardContent>
      )}
      <ul className="divide-y pb-2">
        {rows.map((x) => {
          const inner = (
            <>
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-1.5 text-sm font-semibold">
                  {x.symbol}
                  {x.live && <span className="size-1.5 rounded-full bg-positive" title="Live price" aria-label="live price" />}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  {x.name} · {x.account}
                </p>
              </div>
              <div className="text-right">
                <Money cents={x.value_cents} className="text-sm font-medium" />
                <Change percent={sort === "gain" ? (gain(x) ?? 0) * 100 : x.change_today_percent} label={sort === "gain" ? "total" : undefined} />
              </div>
            </>
          );
          const key = `${x.symbol}-${x.account}`;
          return hasDetail(x.symbol, x.asset_class) ? (
            <li key={key}>
              <Link href={holdingHref(x.symbol)} className="flex items-center gap-3 px-5 py-3.5 transition-colors hover:bg-muted/50">
                {inner}
                <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              </Link>
            </li>
          ) : (
            <li key={key} className="flex items-center gap-3 px-5 py-3.5 pr-12">
              {inner}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function SearchBox({
  value,
  onChange,
  label,
  placeholder,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  placeholder: string;
  className?: string;
}) {
  return (
    <div className={cn("relative", className)}>
      <Search className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
      <Input
        type="search"
        enterKeyHint="search"
        autoComplete="off"
        spellCheck={false}
        maxLength={60}
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && onChange("")}
        className="rounded-full pr-10 pl-10 [&::-webkit-search-cancel-button]:hidden"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          className="absolute top-1/2 right-2 grid size-8 -translate-y-1/2 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
          aria-label="Clear search"
        >
          <X className="size-4" aria-hidden />
        </button>
      )}
    </div>
  );
}

function Change({ percent, label }: { percent: number; label?: string }) {
  return (
    <p className={cn("text-xs tabular-nums", percent > 0 ? "text-positive" : percent < 0 ? "text-negative" : "text-muted-foreground")}>
      {percent > 0 ? "+" : ""}
      {percent.toFixed(2)}%{label && ` ${label}`}
    </p>
  );
}
