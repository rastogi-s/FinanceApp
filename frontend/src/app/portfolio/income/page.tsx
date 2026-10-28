"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { CalendarDays, ChevronDown, ChevronRight, Coins, Landmark, Pencil, Receipt, Repeat } from "lucide-react";
import { MonthBars, TwoLines } from "@/components/income-charts";
import { Money, formatMoney } from "@/components/money";
import { BackLink, Notice, Wide } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { useApi } from "@/lib/household";
import {
  FREQUENCY,
  HORIZONS,
  type DividendHolding,
  type Income,
  type InterestAccount,
  type Estimate,
  type TaxYear,
  WINDOWS,
  type Window,
  type WindowAccount,
  apyOf,
  estimateFor,
  cdWhatIf,
  dayLabel,
  monthLabel,
  pct,
  projectDividends,
  projectInterest,
} from "@/lib/income";
import { useRemembered } from "@/lib/nav";
import { hasDetail, holdingHref } from "@/lib/portfolio";
import { cn } from "@/lib/utils";

type Tab = "dividends" | "interest";

export default function IncomePage() {
  const { data, error } = useApi<Income>("/api/income");
  const [local, setLocal] = useState<Income | null>(null);
  const [tab, setTab] = useRemembered<Tab>("tab", "dividends", { valid: (v) => v === "dividends" || v === "interest" });
  const p = local ?? data;
  return (
    <>
      <Wide />
      <BackLink href="/portfolio">Portfolio</BackLink>
      <div className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Dividends & interest</h1>
        <p className="mt-1 text-sm text-muted-foreground">What your money paid you, what&apos;s coming next, and where reinvesting takes it.</p>
      </div>
      {error && !p && (
        <Notice icon={<Coins className="size-5" />} title="Couldn't load dividends and interest">
          {error}. Check that the server is running, then reload the page.
        </Notice>
      )}
      {!p && !error && (
        <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-44 rounded-xl" />
          <Skeleton className="h-72 rounded-xl" />
          <Skeleton className="h-72 rounded-xl" />
        </div>
      )}
      {p && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Summary p={p} />
          <Ahead e={p.estimate} />
          <div className="md:col-span-2">
            <div className="grid grid-cols-2 gap-1 rounded-full bg-muted p-1" role="radiogroup" aria-label="Show">
              {(["dividends", "interest"] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  role="radio"
                  aria-checked={tab === t}
                  onClick={() => setTab(t)}
                  className={cn(
                    "h-10 rounded-full text-sm font-medium transition-colors",
                    tab === t ? "bg-card text-foreground shadow-sm" : "text-muted-foreground",
                  )}
                >
                  {t === "dividends" ? "Dividends" : "Interest"}
                </button>
              ))}
            </div>
          </div>
          {tab === "dividends" ? <Dividends p={p} /> : <Interest p={p} onSaved={setLocal} />}
          <Taxes years={p.tax} />
        </div>
      )}
    </>
  );
}

// ---- the top: both kinds together

function Summary({ p }: { p: Income }) {
  const d = p.dividends;
  const i = p.interest;
  const total = d.received_12m_cents + i.received_12m_cents;
  const prior = d.received_prior_12m_cents;
  const change = prior ? ((d.received_12m_cents - prior) / prior) * 100 : null;
  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>Paid to you, last 12 months</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-4xl font-semibold tracking-tight">
          <Money cents={total} />
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          About <Money cents={Math.round(total / 12)} /> a month, without lifting a finger.
        </p>
        <div className="mt-5 grid grid-cols-3 gap-2 sm:gap-3">
          <Stat label="Dividends" value={<Money cents={d.received_12m_cents} />}>
            {change !== null && (
              <span className={change >= 0 ? "text-positive" : "text-negative"}>
                {change >= 0 ? "+" : "−"}
                {Math.abs(change).toFixed(1)}% on the year before
              </span>
            )}
          </Stat>
          <Stat label="Interest" value={<Money cents={i.received_12m_cents} />}>
            {i.brokerage_12m_cents > 0 && (
              <>
                incl. <Money cents={i.brokerage_12m_cents} /> on brokerage cash
              </>
            )}
          </Stat>
          <Stat label="Next 12 months" value={<Money cents={p.estimate.annual_cents} />}>
            At today&apos;s holdings and rates
          </Stat>
        </div>
      </CardContent>
    </Card>
  );
}

// ---- what to expect: the next month to the next year, in all or by account

const WINDOW_LABEL: Record<Window, string> = { 1: "1 month", 2: "2 months", 3: "3 months", 6: "6 months", 12: "1 year" };

function Ahead({ e }: { e: Estimate }) {
  const [months, setMonths] = useRemembered<Window>("ahead", 12, { valid: (v) => (WINDOWS as readonly number[]).includes(v) });
  const [split, setSplit] = useRemembered("ahead-by-account", false);
  const w = useMemo(() => estimateFor(e, months), [e, months]);
  const label = months === 12 ? "a year" : months === 1 ? "in the next month" : `in the next ${months} months`;
  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>Expected from what you hold now</CardTitle>
        <Badge variant="muted">Estimate</Badge>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-5 gap-1 rounded-full bg-muted p-1" role="radiogroup" aria-label="Over the next">
          {WINDOWS.map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={months === m}
              onClick={() => setMonths(m)}
              className={cn(
                "h-9 rounded-full text-xs font-medium whitespace-nowrap transition-colors sm:text-sm",
                months === m ? "bg-card text-foreground shadow-sm" : "text-muted-foreground",
              )}
            >
              {WINDOW_LABEL[m]}
            </button>
          ))}
        </div>
        <p className="mt-5 text-4xl font-semibold tracking-tight">
          <Money cents={w.totalCents} />
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          Dividends and interest {label}
          {months === 12 && (
            <>
              , about <Money cents={Math.round(w.totalCents / 12)} /> a month
            </>
          )}
          .
        </p>
        <div className="mt-4 grid grid-cols-2 gap-2 sm:gap-3">
          <Stat label="Dividends" value={<Money cents={w.dividendsCents} />}>
            {months === 12 ? "Last 12 months per share × shares now" : "Payments due at each holding's usual schedule"}
          </Stat>
          <Stat label="Interest" value={<Money cents={w.interestCents} />}>
            Savings, CDs and brokerage cash at today&apos;s rates
          </Stat>
        </div>

        <div className="mt-5">
          <Switch checked={split} onCheckedChange={setSplit} label="By account" description="What each account is expected to pay" />
        </div>
        {split && (
          <ul className="mt-2 divide-y">
            {w.accounts.map((a) => (
              <AheadRow key={a.account} a={a} total={w.totalCents} />
            ))}
            {w.accounts.length === 0 && <li className="py-3 text-sm text-muted-foreground">Nothing expected yet.</li>}
          </ul>
        )}
        <p className="mt-3 text-xs text-muted-foreground">
          An estimate, not a promise: dividends change when companies and funds change them, and bank rates move. Interest uses
          the APY you typed in, or what the last three months of interest work out to.
        </p>
      </CardContent>
    </Card>
  );
}

function AheadRow({ a, total }: { a: WindowAccount; total: number }) {
  const share = total ? (a.totalCents / total) * 100 : 0;
  const detail =
    a.kind === "investment"
      ? [
          a.payers.length ? a.payers.slice(0, 3).map((x) => x.symbol).join(", ") : null,
          a.dividend_yield_percent !== null ? `${a.dividend_yield_percent.toFixed(2)}% yield` : null,
          !a.payers.length && a.interest_annual_cents > 0 ? "Interest on cash" : null,
        ]
          .filter(Boolean)
          .join(" · ")
      : `${a.apy?.toFixed(2)}% APY${a.rate_from === "recent" ? " from recent interest" : ""}`;
  return (
    <li className="flex items-center gap-3 py-2.5">
      <span className="grid size-8 shrink-0 place-items-center rounded-full bg-muted text-muted-foreground">
        {a.kind === "investment" ? <Coins className="size-4" aria-hidden /> : <Landmark className="size-4" aria-hidden />}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{a.account}</p>
        <p className="truncate text-xs text-muted-foreground">{detail}</p>
        <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-muted" aria-hidden>
          <div className="h-full rounded-full bg-primary/70" style={{ width: `${share}%` }} />
        </div>
      </div>
      <div className="shrink-0 text-right">
        <Money cents={a.totalCents} className="text-sm font-medium tabular-nums" />
        {a.dividendsCents > 0 && a.interestCents > 0 && (
          <p className="text-[11px] text-muted-foreground tabular-nums">
            <Money cents={a.dividendsCents} /> + <Money cents={a.interestCents} />
          </p>
        )}
      </div>
    </li>
  );
}

function Stat({ label, value, children }: { label: string; value: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-xl bg-muted/50 px-3 py-3 sm:px-4">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-base font-semibold tracking-tight tabular-nums sm:text-xl">{value}</p>
      {children && <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground sm:text-xs">{children}</p>}
    </div>
  );
}

function since(p: Income) {
  return dayLabel(p.history_from, true);
}

// ---- dividends

function Dividends({ p }: { p: Income }) {
  const d = p.dividends;
  if (d.holdings.length === 0 && d.received_12m_cents === 0) {
    return (
      <div className="md:col-span-2">
        <Notice icon={<Coins className="size-5" />} title="No dividends yet">
          Dividends show up here from your brokerage&apos;s activity, up to two years back, after the next sync. Holdings that pay
          dividends also show what to expect once their price history is looked up (once a day).
        </Notice>
      </div>
    );
  }
  const bars = [
    ...d.by_month.slice(-12).map((m) => ({ ...m, expected: false })),
    ...d.expected_months.slice(1).map((m) => ({ ...m, expected: true })),
  ];
  // The current month: what came in so far, plus what's still expected this month.
  const thisMonth = d.expected_months[0];
  if (thisMonth && bars[11]) bars[11] = { ...bars[11], cents: bars[11].cents + thisMonth.cents };
  return (
    <>
      <Card className="md:col-span-2">
        <CardHeader>
          <CardTitle>Dividends by month</CardTitle>
          <span className="text-sm font-medium">
            <Money cents={d.forward_annual_cents} /> a year expected
          </span>
        </CardHeader>
        <CardContent>
          <MonthBars bars={bars} label="Dividends by month, the last year and the next" hint="Drag across to read a month" />
          <p className="mt-3 text-xs text-muted-foreground">
            The last 12 months as paid, then the next 11 at each holding&apos;s usual schedule and latest dividend. Your history starts{" "}
            {since(p)}.
          </p>
        </CardContent>
      </Card>

      <KeyNumbers p={p} />
      <UpcomingCard p={p} />
      <Projection holdings={d.holdings} reinvesting={d.reinvesting} />
      <Payers p={p} />
      <ByYear rows={d.by_year} from={p.history_from} title="Dividends by year" />
      <Recent p={p} />
    </>
  );
}

function KeyNumbers({ p }: { p: Income }) {
  const d = p.dividends;
  const ytdChange =
    d.last_year_same_period_cents && d.last_year_same_period_cents > 0
      ? ((d.this_year_cents - d.last_year_same_period_cents) / d.last_year_same_period_cents) * 100
      : null;
  const top = d.holdings[0];
  const tiles = [
    {
      label: "Yield",
      value: pct(d.yield_percent),
      note: (
        <>
          Expected dividends over <Money cents={d.invested_cents} /> invested
        </>
      ),
    },
    {
      label: "Yield on cost",
      value: pct(d.yield_on_cost_percent),
      note: "Over what you paid for the payers",
    },
    {
      label: "This year so far",
      value: <Money cents={d.this_year_cents} />,
      note:
        ytdChange !== null ? (
          <span className={ytdChange >= 0 ? "text-positive" : "text-negative"}>
            {ytdChange >= 0 ? "+" : "−"}
            {Math.abs(ytdChange).toFixed(1)}% vs this time last year
          </span>
        ) : (
          "No full year before to compare"
        ),
    },
    {
      label: "Biggest payer",
      value: top ? top.symbol : "—",
      note: d.top_share_percent !== null ? `${d.top_share_percent.toFixed(0)}% of your dividends; top 3: ${d.top3_share_percent?.toFixed(0)}%` : "",
    },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Key numbers</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-3">
        {tiles.map((t) => (
          <div key={t.label} className="rounded-xl bg-muted/50 px-3.5 py-3">
            <p className="text-xs font-medium text-muted-foreground">{t.label}</p>
            <p className="money mt-0.5 text-lg font-semibold tracking-tight tabular-nums">{t.value}</p>
            <p className="mt-0.5 text-xs leading-snug text-muted-foreground">{t.note}</p>
          </div>
        ))}
        {d.capital_gains_12m_cents > 0 && (
          <p className="col-span-2 text-xs text-muted-foreground">
            Funds also paid <Money cents={d.capital_gains_12m_cents} /> in capital gain distributions in the last 12 months (not counted as dividends).
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function UpcomingCard({ p }: { p: Income }) {
  const [all, setAll] = useState(false);
  const rows = p.dividends.upcoming;
  const shown = all ? rows : rows.slice(0, 6);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Coming up</CardTitle>
        <CalendarDays className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="pt-1">
        {rows.length === 0 && <p className="text-sm text-muted-foreground">Nothing expected in the next few months.</p>}
        <ul className="divide-y">
          {shown.map((u) => (
            <li key={`${u.symbol}-${u.pay_date}`} className="flex items-center gap-3 py-2.5">
              <div className="grid w-11 shrink-0 place-items-center rounded-lg bg-muted/70 py-1 text-center leading-tight">
                <span className="text-[10px] font-medium text-muted-foreground uppercase">{monthLabel(u.pay_date.slice(0, 7))}</span>
                <span className="text-base font-semibold tabular-nums">{Number(u.pay_date.slice(8, 10))}</span>
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">
                  {u.symbol} <span className="font-normal text-muted-foreground">· {u.name}</span>
                </p>
                <p className="text-xs text-muted-foreground">
                  Own it before {dayLabel(u.ex_date)} (ex-dividend){u.reinvesting && " · reinvested"}
                </p>
              </div>
              <Money cents={u.cents} className="shrink-0 text-sm font-medium tabular-nums" />
            </li>
          ))}
        </ul>
        {rows.length > 6 && (
          <Button variant="ghost" size="sm" className="mt-1 -ml-2" onClick={() => setAll(!all)}>
            {all ? "Show fewer" : `Show all ${rows.length}`}
          </Button>
        )}
        <p className="mt-2 text-xs text-muted-foreground">
          Estimated from each holding&apos;s past dividends: dates follow its usual schedule and the amount is the latest dividend
          times the shares you hold now. The company or fund announces the real one a few weeks ahead.
        </p>
      </CardContent>
    </Card>
  );
}

function Projection({ holdings, reinvesting }: { holdings: DividendHolding[]; reinvesting: string[] }) {
  const [drip, setDrip] = useRemembered("drip", true);
  const [priceGrowth, setPriceGrowth] = useRemembered("price-growth", 5);
  const [own, setOwn] = useRemembered("own-growth", true);
  const [divGrowth, setDivGrowth] = useRemembered("dividend-growth", 5);
  const [horizon, setHorizon] = useRemembered<number>("horizon", 10, { valid: (v) => (HORIZONS as readonly number[]).includes(v) });

  const scenario = { priceGrowth: priceGrowth / 100, dividendGrowth: own ? null : divGrowth / 100 };
  const on = useMemo(() => projectDividends(holdings, { ...scenario, drip: true }), [holdings, scenario.priceGrowth, scenario.dividendGrowth]); // eslint-disable-line react-hooks/exhaustive-deps
  const off = useMemo(() => projectDividends(holdings, { ...scenario, drip: false }), [holdings, scenario.priceGrowth, scenario.dividendGrowth]); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = drip ? on : off;
  const at = pick[horizon - 1]!;
  const other = (drip ? off : on)[horizon - 1]!;
  const weighted = holdings.reduce((s, h) => s + h.annual_cents, 0);
  const avgGrowth = weighted
    ? holdings.reduce((s, h) => s + h.annual_cents * (h.growth_5y ?? 0.04), 0) / weighted
    : 0.04;

  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>Where it goes</CardTitle>
        <Repeat className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-6 md:grid-cols-[1fr_18rem]">
        <div className="min-w-0">
          <div className="mb-4 grid grid-cols-4 gap-1 rounded-full bg-muted p-1" role="radiogroup" aria-label="Years from now">
            {HORIZONS.map((y) => (
              <button
                key={y}
                type="button"
                role="radio"
                aria-checked={horizon === y}
                onClick={() => setHorizon(y)}
                className={cn(
                  "h-9 rounded-full text-sm font-medium transition-colors",
                  horizon === y ? "bg-card text-foreground shadow-sm" : "text-muted-foreground",
                )}
              >
                {y} {y === 1 ? "year" : "years"}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-muted/50 px-4 py-3">
              <p className="text-xs font-medium text-muted-foreground">Dividends in year {horizon}</p>
              <p className="money mt-0.5 text-2xl font-semibold tracking-tight tabular-nums">{formatMoney(at.incomeCents)}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {formatMoney(Math.round(at.incomeCents / 12))} a month · {drip ? "without reinvesting" : "reinvesting"}:{" "}
                <span className="money">{formatMoney(other.incomeCents)}</span>
              </p>
            </div>
            <div className="rounded-xl bg-muted/50 px-4 py-3">
              <p className="text-xs font-medium text-muted-foreground">These holdings worth</p>
              <p className="money mt-0.5 text-2xl font-semibold tracking-tight tabular-nums">{formatMoney(at.valueCents, "compact")}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {drip ? (
                  <>
                    incl. <span className="money">{formatMoney(at.addedShareValueCents, "compact")}</span> of shares bought with dividends
                  </>
                ) : (
                  <>
                    plus <span className="money">{formatMoney(at.collectedCents, "compact")}</span> of dividends taken as cash
                  </>
                )}
              </p>
            </div>
          </div>
          <div className="mt-5">
            <TwoLines
              on={on.map((r) => r.incomeCents)}
              off={off.map((r) => r.incomeCents)}
              onLabel="Reinvesting"
              offLabel="Taken as cash"
              highlight={drip ? "on" : "off"}
              label="Yearly dividends for the next 20 years, reinvesting and not"
            />
          </div>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm tabular-nums">
              <caption className="sr-only">Yearly dividends and value, reinvesting and not</caption>
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th scope="col" className="py-1.5 font-medium">In</th>
                  <th scope="col" className="py-1.5 text-right font-medium">Reinvesting</th>
                  <th scope="col" className="py-1.5 text-right font-medium">As cash</th>
                  <th scope="col" className="hidden py-1.5 text-right font-medium sm:table-cell">Extra from DRIP</th>
                </tr>
              </thead>
              <tbody className="money divide-y">
                {HORIZONS.map((y) => (
                  <tr key={y} className={cn(y === horizon && "font-semibold")}>
                    <th scope="row" className="py-2 text-left font-medium">
                      {y} {y === 1 ? "year" : "years"}
                    </th>
                    <td className="py-2 text-right">{formatMoney(on[y - 1]!.incomeCents)}/yr</td>
                    <td className="py-2 text-right">{formatMoney(off[y - 1]!.incomeCents)}/yr</td>
                    <td className="hidden py-2 text-right text-positive sm:table-cell">+{formatMoney(on[y - 1]!.incomeCents - off[y - 1]!.incomeCents)}/yr</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="order-first grid content-start gap-5 md:order-none">
          <Switch
            checked={drip}
            onCheckedChange={setDrip}
            label="Reinvest dividends (DRIP)"
            description={
              reinvesting.length
                ? `Your brokerage already reinvests ${reinvesting.join(", ")}.`
                : "Each dividend buys more shares, which pay dividends too."
            }
          />
          <Slider
            label="Price growth a year"
            value={priceGrowth}
            min={0}
            max={10}
            step={0.5}
            display={`${priceGrowth}%`}
            onChange={setPriceGrowth}
            hint="Reinvested dividends buy fewer shares when prices rise faster."
          />
          <Switch
            checked={own}
            onCheckedChange={setOwn}
            label="Use each holding's own dividend growth"
            description={`Its last five years; about ${(avgGrowth * 100).toFixed(1)}% a year across yours.`}
          />
          {!own && (
            <Slider
              label="Dividend growth a year"
              value={divGrowth}
              min={0}
              max={12}
              step={0.5}
              display={`${divGrowth}%`}
              onChange={setDivGrowth}
            />
          )}
          <p className="text-xs text-muted-foreground">
            Only the holdings you have today, no new money added, before taxes. Not a promise: dividends can be cut.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function Payers({ p }: { p: Income }) {
  const [open, setOpen] = useRemembered<string | null>("payer-open", null);
  const rows = p.dividends.holdings;
  const total = rows.reduce((s, r) => s + r.annual_cents, 0);
  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>By holding</CardTitle>
        {p.dividends.non_payers_cents > 0 && (
          <span className="text-xs text-muted-foreground">
            <Money cents={p.dividends.non_payers_cents} /> pays nothing
          </span>
        )}
      </CardHeader>
      <CardContent className="pt-1">
        <ul className="divide-y">
          {rows.map((r) => {
            const share = total ? (r.annual_cents / total) * 100 : 0;
            const isOpen = open === r.symbol;
            return (
              <li key={r.symbol}>
                <button
                  type="button"
                  aria-expanded={isOpen}
                  onClick={() => setOpen(isOpen ? null : r.symbol)}
                  className="-mx-2 grid w-[calc(100%+1rem)] grid-cols-[1fr_auto] items-center gap-x-3 rounded-lg px-2 py-2.5 text-left hover:bg-muted/50"
                >
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5 text-sm">
                      <span className="max-w-[60%] shrink-0 truncate font-semibold" title={r.symbol}>
                        {r.symbol}
                      </span>
                      <span className="truncate text-muted-foreground">{r.name}</span>
                      {r.reinvesting && <Badge variant="muted" className="shrink-0 px-1.5 py-0 text-[10px]">DRIP</Badge>}
                    </span>
                    <span className="mt-1.5 block h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
                      <span className="block h-full rounded-full bg-chart-1" style={{ width: `${share}%` }} />
                    </span>
                  </span>
                  <span className="flex items-center gap-1.5 text-right">
                    <span>
                      <Money cents={r.annual_cents} className="block text-sm font-medium tabular-nums" />
                      <span className="block text-xs text-muted-foreground tabular-nums">
                        {pct(r.yield_percent)} · {share.toFixed(0)}%
                      </span>
                    </span>
                    <ChevronDown className={cn("size-4 text-muted-foreground transition-transform", isOpen && "rotate-180")} aria-hidden />
                  </span>
                </button>
                {isOpen && <PayerDetail r={r} />}
              </li>
            );
          })}
        </ul>
        <p className="mt-2 text-xs text-muted-foreground">
          A year of dividends at the last 12 months&apos; rate per share, times the shares you hold now. Yield is that over today&apos;s
          price.
        </p>
      </CardContent>
    </Card>
  );
}

function PayerDetail({ r }: { r: DividendHolding }) {
  const facts: [string, React.ReactNode][] = [
    ["Pays", FREQUENCY[r.frequency] ?? `${r.frequency} times a year`],
    ["Per share, last 12 months", `$${r.annual_per_share.toFixed(2)}`],
    ["Latest dividend", r.last_ex_date ? `$${r.last_amount.toFixed(4)} · ex ${dayLabel(r.last_ex_date, true)}` : "—"],
    ["Next (estimated)", r.next_pay_date ? <>{dayLabel(r.next_pay_date, true)} · <Money cents={r.next_cents ?? 0} /></> : "—"],
    ["Yield on cost", pct(r.yield_on_cost_percent)],
    ["Dividend growth, 5 years", r.growth_5y !== null ? `${(r.growth_5y * 100).toFixed(1)}% a year` : "Not enough history"],
    ["Received, last 12 months", <Money key="r" cents={r.received_12m_cents} />],
    ["Held in", r.accounts.join(", ")],
  ];
  return (
    <div className="mb-3 rounded-xl bg-muted/40 px-4 py-3">
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
        {facts.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-3">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="text-right font-medium tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      {r.from === "payments" && (
        <p className="mt-2 text-xs text-muted-foreground">No dividend history from the quote service yet, so this is from what you were paid.</p>
      )}
      {hasDetail(r.symbol, r.asset_class) && (
        <Link href={holdingHref(r.symbol)} className="mt-2 inline-flex items-center gap-0.5 text-sm font-medium text-primary">
          Open {r.symbol} <ChevronRight className="size-4" aria-hidden />
        </Link>
      )}
    </div>
  );
}

function ByYear({ rows, from, title }: { rows: { year: number; cents: number }[]; from: string; title: string }) {
  const max = Math.max(...rows.map((r) => r.cents), 1);
  const firstYear = Number(from.slice(0, 4));
  const partialStart = from.slice(5) !== "01-01";
  const thisYear = new Date().getFullYear();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="pt-1">
        {rows.length === 0 && <p className="text-sm text-muted-foreground">Nothing yet.</p>}
        <ul className="grid gap-3">
          {[...rows].reverse().map((r) => (
            <li key={r.year}>
              <div className="mb-1 flex items-baseline justify-between text-sm">
                <span className="font-medium">
                  {r.year}
                  {r.year === thisYear && <span className="font-normal text-muted-foreground"> · so far</span>}
                  {r.year === firstYear && partialStart && <span className="font-normal text-muted-foreground"> · from {dayLabel(from)}</span>}
                </span>
                <Money cents={r.cents} className="tabular-nums" />
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-muted" aria-hidden>
                <div className="h-full rounded-full bg-chart-1" style={{ width: `${(r.cents / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function Recent({ p }: { p: Income }) {
  const rows = p.dividends.recent;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Latest payments</CardTitle>
      </CardHeader>
      <CardContent className="pt-1">
        {rows.length === 0 && <p className="text-sm text-muted-foreground">None in the last two years.</p>}
        <ul className="divide-y">
          {rows.map((r, i) => (
            <li key={i} className="flex items-center gap-3 py-2 text-sm">
              <span className="w-14 shrink-0 text-xs text-muted-foreground tabular-nums">{dayLabel(r.date)}</span>
              <span className="min-w-0 flex-1 truncate">
                <span className="font-medium">{r.symbol ?? r.name}</span>
                <span className="text-muted-foreground"> · {r.account}</span>
              </span>
              <Money cents={r.cents} className="shrink-0 tabular-nums" />
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

// ---- interest

function Interest({ p, onSaved }: { p: Income; onSaved: (p: Income) => void }) {
  const i = p.interest;
  const [shift, setShift] = useRemembered("rate-shift", 0);
  const [editing, setEditing] = useState<InterestAccount | null>(null);
  const withRate = i.accounts.filter((a) => apyOf(a) !== null);
  const years = useMemo(() => projectInterest(i.accounts, shift, 10), [i.accounts, shift]);
  const missing = i.accounts.filter((a) => apyOf(a) === null && a.balance_cents > 0);

  if (i.accounts.length === 0 && i.received_12m_cents === 0) {
    return (
      <div className="md:col-span-2">
        <Notice icon={<Landmark className="size-5" />} title="No interest yet">
          Interest from savings accounts, CDs and money market accounts shows up here once a bank account is linked and has paid
          some. Brokerage cash interest shows up too.
        </Notice>
      </div>
    );
  }
  return (
    <>
      <Card className="md:col-span-2">
        <CardHeader>
          <CardTitle>Interest by month</CardTitle>
          <span className="text-sm font-medium">
            <Money cents={i.this_year_cents} /> this year
          </span>
        </CardHeader>
        <CardContent>
          <MonthBars bars={i.by_month} label="Interest by month, the last two years" hint="Drag across to read a month" />
          <p className="mt-3 text-xs text-muted-foreground">From your bank accounts and brokerage cash. History starts {since(p)}.</p>
        </CardContent>
      </Card>

      <Card className="md:col-span-2">
        <CardHeader>
          <CardTitle>Accounts</CardTitle>
          <Landmark className="size-4 text-muted-foreground" aria-hidden />
        </CardHeader>
        <CardContent className="pt-1">
          <ul className="divide-y">
            {i.accounts.map((a) => (
              <AccountRow key={a.id} a={a} onEdit={() => setEditing(a)} />
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted-foreground">
            Banks don&apos;t share their rates, so the APY is estimated from the last three months of interest until you type it in.
          </p>
        </CardContent>
      </Card>

      <Card className="md:col-span-2">
        <CardHeader>
          <CardTitle>Interest ahead</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-6 md:grid-cols-[1fr_18rem]">
          <div className="min-w-0">
            <div className="grid grid-cols-3 gap-3">
              {[1, 5, 10].map((y) => (
                <div key={y} className="rounded-xl bg-muted/50 px-3.5 py-3">
                  <p className="text-xs font-medium text-muted-foreground">{y === 1 ? "Next 12 months" : `Year ${y}`}</p>
                  <p className="money mt-0.5 text-lg font-semibold tracking-tight tabular-nums">{formatMoney(years[y - 1]!.interestCents)}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {y === 1 ? (
                      <>{formatMoney(Math.round(years[0]!.interestCents / 12))}/mo</>
                    ) : (
                      <span className="money">{formatMoney(years[y - 1]!.cumulativeCents, "compact")} total</span>
                    )}
                  </p>
                </div>
              ))}
            </div>
            <ul className="mt-4 grid gap-2 text-sm">
              {withRate.map((a) => {
                const one = projectInterest([a], shift, 1)[0]!;
                return (
                  <li key={a.id} className="flex justify-between gap-3">
                    <span className="min-w-0 truncate text-muted-foreground">{a.name}</span>
                    <span className="shrink-0 tabular-nums">
                      <Money cents={one.interestCents} /> <span className="text-muted-foreground">next 12 months</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
          <div className="grid content-start gap-4">
            <Slider
              label="If rates change by"
              value={shift}
              min={-3}
              max={2}
              step={0.25}
              display={`${shift > 0 ? "+" : shift < 0 ? "−" : ""}${Math.abs(shift).toFixed(2)} pts`}
              onChange={setShift}
              hint="Savings rates follow the Fed. Slide left to see a cut."
            />
            <p className="text-xs text-muted-foreground">
              Interest left in the account and compounding monthly at today&apos;s balances, no deposits or withdrawals.
              {missing.length > 0 && ` ${missing.map((a) => a.name).join(", ")} ${missing.length === 1 ? "has" : "have"} no rate yet.`}
            </p>
          </div>
        </CardContent>
      </Card>

      {i.accounts
        .filter((a) => a.kind === "cd")
        .map((a) => (
          <CdCard key={a.id} a={a} onEdit={() => setEditing(a)} />
        ))}
      <ByYear rows={i.by_year} from={p.history_from} title="Interest by year" />
      <RateSheet account={editing} onClose={() => setEditing(null)} onSaved={onSaved} demo={!!p.demo} />
    </>
  );
}

const KIND_LABEL: Record<string, string> = { cd: "CD", savings: "Savings", "money market": "Money market", checking: "Checking", hsa: "HSA" };

function AccountRow({ a, onEdit }: { a: InterestAccount; onEdit: () => void }) {
  const rate = apyOf(a);
  return (
    <li className="flex items-center gap-3 py-3">
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-1.5 text-sm font-medium">
          <span className="truncate">{a.name}</span>
          <Badge variant="muted" className="shrink-0 px-1.5 py-0 text-[10px]">{KIND_LABEL[a.kind] ?? a.kind}</Badge>
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          <Money cents={a.balance_cents} /> ·{" "}
          {rate !== null ? (
            <>
              {a.apy !== null ? "" : "about "}
              {rate.toFixed(2)}% APY{a.apy === null && " (estimated)"}
            </>
          ) : (
            "no rate yet"
          )}
          {a.maturity && <> · matures {dayLabel(a.maturity, true)}</>}
        </p>
      </div>
      <div className="shrink-0 text-right">
        <Money cents={a.interest_12m_cents} className="block text-sm font-medium tabular-nums" />
        <span className="block text-xs text-muted-foreground">last 12 months</span>
      </div>
      <Button variant="ghost" size="icon" onClick={onEdit} aria-label={`Set the rate for ${a.name}`}>
        <Pencil />
      </Button>
    </li>
  );
}

function CdCard({ a, onEdit }: { a: InterestAccount; onEdit: () => void }) {
  const base = apyOf(a) ?? 4;
  const [rate, setRate] = useState(Math.max(0, Math.round((base - 0.5) * 4) / 4));
  const [term, setTerm] = useState(a.term_months ?? 12);
  const [now] = useState(() => Date.now());
  if (!a.maturity) {
    return (
      <Card className="md:col-span-2">
        <CardHeader>
          <CardTitle>{a.name}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Add the maturity date to see what it earns until then, and what rolling it over would.</p>
          <Button size="sm" variant="outline" className="mt-3" onClick={onEdit}>
            Add maturity date
          </Button>
        </CardContent>
      </Card>
    );
  }
  const w = cdWhatIf(a, rate, term);
  const days = Math.round((new Date(a.maturity + "T12:00:00").getTime() - now) / 86_400_000);
  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>{a.name}</CardTitle>
        <Badge variant={days <= 30 ? "warning" : "muted"} className="shrink-0 whitespace-nowrap">{days > 0 ? `Matures in ${days} days` : "Matured"}</Badge>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-6 md:grid-cols-[1fr_18rem]">
        <div>
          <p className="text-sm">
            At {(apyOf(a) ?? 0).toFixed(2)}% it earns <Money cents={w.toMaturityCents} className="font-semibold" /> more by{" "}
            {dayLabel(a.maturity, true)}, for <Money cents={w.atMaturityCents} className="font-semibold" /> at maturity.
          </p>
          <div className="mt-4 rounded-xl bg-muted/50 px-4 py-3">
            <p className="text-xs font-medium text-muted-foreground">Roll it over for {term} months at {rate.toFixed(2)}%</p>
            <p className="money mt-0.5 text-2xl font-semibold tracking-tight tabular-nums">+{formatMoney(w.rolloverCents)}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              <span className="money">{formatMoney(w.endCents)}</span> when the new term ends
            </p>
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            Banks usually renew a CD automatically at their current rate unless you act in the grace period after it matures
            (often 7 to 10 days).
          </p>
        </div>
        <div className="grid content-start gap-5">
          <Slider label="New rate" value={rate} min={0} max={7} step={0.05} display={`${rate.toFixed(2)}%`} onChange={setRate} />
          <Slider label="New term" value={term} min={3} max={60} step={3} display={`${term} months`} onChange={setTerm} />
        </div>
      </CardContent>
    </Card>
  );
}

function RateSheet({
  account,
  onClose,
  onSaved,
  demo,
}: {
  account: InterestAccount | null;
  onClose: () => void;
  onSaved: (p: Income) => void;
  demo: boolean;
}) {
  return (
    <Sheet
      open={account !== null}
      onOpenChange={(o) => !o && onClose()}
      title={account ? `Rate for ${account.name}` : "Rate"}
      description="From the bank's site or app. Used for the projections."
    >
      {account && <RateForm key={account.id} account={account} onClose={onClose} onSaved={onSaved} demo={demo} />}
    </Sheet>
  );
}

function RateForm({
  account,
  onClose,
  onSaved,
  demo,
}: {
  account: InterestAccount;
  onClose: () => void;
  onSaved: (p: Income) => void;
  demo: boolean;
}) {
  const [apy, setApy] = useState(account.apy?.toString() ?? "");
  const [maturity, setMaturity] = useState(account.maturity ?? "");
  const [term, setTerm] = useState(account.term_months?.toString() ?? "");
  const [saving, setSaving] = useState(false);
  const isCd = account.kind === "cd";
  const apyNumber = apy.trim() === "" ? null : Number(apy);
  const bad = apyNumber !== null && (!Number.isFinite(apyNumber) || apyNumber < 0 || apyNumber > 25);

  const save = async (clear = false) => {
    if (demo) return toast("Rates can't be changed while the app shows sample data.");
    setSaving(true);
    try {
      const body = clear
        ? {}
        : { apy: apyNumber, maturity: isCd && maturity ? maturity : null, term_months: isCd && term ? Number(term) : null };
      onSaved(await api<Income>(`/api/income/accounts/${account.id}`, { method: "PUT", body }));
      toast(clear ? "Back to the estimated rate." : "Saved.");
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <form
      className="grid grid-cols-1 gap-4 pb-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (!bad) save();
      }}
    >
      <Field
        id="apy"
        label="APY (%)"
        hint={account.implied_apy !== null ? `Recent interest works out to about ${account.implied_apy.toFixed(2)}%.` : undefined}
        error={bad ? "Enter a rate between 0 and 25." : null}
      >
        <Input inputMode="decimal" value={apy} onChange={(e) => setApy(e.target.value)} placeholder="4.25" autoComplete="off" />
      </Field>
      {isCd && (
        <>
          <Field id="maturity" label="Matures on">
            <Input type="date" value={maturity} onChange={(e) => setMaturity(e.target.value)} />
          </Field>
          <Field id="term" label="Term (months)" hint="How long it was opened for, e.g. 12.">
            <Input inputMode="numeric" value={term} onChange={(e) => setTerm(e.target.value.replace(/\D/g, ""))} placeholder="12" />
          </Field>
        </>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={saving || bad} className="flex-1">
          Save
        </Button>
        {(account.apy !== null || account.maturity) && (
          <Button type="button" variant="outline" disabled={saving} onClick={() => save(true)}>
            Clear
          </Button>
        )}
      </div>
    </form>
  );
}

// ---- taxes

function Taxes({ years }: { years: TaxYear[] }) {
  const [year, setYear] = useRemembered<number>("tax-year", years[0]?.year ?? 0, { valid: (v) => years.some((y) => y.year === v) });
  const t = years.find((y) => y.year === year) ?? years[0];
  if (!t) return null;
  const d = t.dividends;
  const rows: [string, number, string?][] = [
    ["Qualified dividends", d.estimated_qualified_cents, "1099-DIV box 1b · lower tax rate"],
    ["Ordinary dividends", d.estimated_ordinary_cents, "taxed like pay"],
    ["Capital gain distributions", d.capital_gain_distributions_cents, "1099-DIV box 2a"],
    ["Interest", t.interest.total_cents, "1099-INT box 1"],
  ];
  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>For taxes</CardTitle>
        <div className="grid grid-cols-2 gap-1 rounded-full bg-muted p-1" role="radiogroup" aria-label="Tax year">
          {years.map((y) => (
            <button
              key={y.year}
              type="button"
              role="radio"
              aria-checked={y.year === t.year}
              onClick={() => setYear(y.year)}
              className={cn(
                "h-8 rounded-full px-3 text-xs font-medium transition-colors",
                y.year === t.year ? "bg-card text-foreground shadow-sm" : "text-muted-foreground",
              )}
            >
              {y.year}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="pt-2">
        <ul className="divide-y">
          {rows.map(([label, cents, note]) => (
            <li key={label} className="flex items-center justify-between gap-3 py-2.5 text-sm">
              <span>
                {label}
                {note && <span className="block text-xs text-muted-foreground">{note}</span>}
              </span>
              <Money cents={cents} className="font-medium tabular-nums" />
            </li>
          ))}
        </ul>
        <div className="mt-3 flex items-start gap-2 text-xs text-muted-foreground">
          <Receipt className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <p>
            Taxable accounts only; <Money cents={t.tax_advantaged_cents} /> earned inside IRAs, 401(k)s and HSAs isn&apos;t taxed now.
            {t.complete ? "" : ` So far this year, through ${dayLabel(t.through)}.`}
            {d.unlabeled_cents > 0 &&
              ` Your brokerage didn't label ${formatMoney(d.unlabeled_cents)} of dividends, so those are estimated: bond and money market funds as ordinary, the rest as qualified.`}
            {!t.covers_whole_year && ` History only reaches back to ${dayLabel(t.history_from, true)}, so this year is incomplete.`}{" "}
            Your 1099s have the final numbers.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
