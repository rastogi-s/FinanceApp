"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ChevronRight, TrendingUp } from "lucide-react";
import { Money } from "@/components/money";
import { BackLink, Column, Columns, Notice, Wide } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useApi } from "@/lib/household";
import { useRemembered } from "@/lib/nav";
import { asOf, hasDetail, holdingHref } from "@/lib/portfolio";
import { type Profit, byHolding, gainOf, picked, signedPercent, soldIn, totals } from "@/lib/profit";
import { cn } from "@/lib/utils";

const shareCount = new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 });
const FIRST_SALES = 8;

export default function ProfitPage() {
  const { data: p, error } = useApi<Profit>("/api/portfolio/profit");
  return (
    <>
      <Wide />
      <BackLink href="/portfolio">Portfolio</BackLink>
      <div className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Profit</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          How much your holdings have grown over what you paid for them, worked out lot by lot.
        </p>
      </div>
      {error && !p && (
        <Notice icon={<TrendingUp className="size-5" />} title="Couldn't work out your profit">
          {error}. Check that the server is running, then reload the page.
        </Notice>
      )}
      {!p && !error && (
        <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-10 w-2/3 rounded-full" />
          <Skeleton className="h-64 rounded-xl" />
          <Skeleton className="h-72 rounded-xl" />
        </div>
      )}
      {p && p.accounts.length === 0 && (
        <Notice icon={<TrendingUp className="size-5" />} title="No holdings yet">
          Link a brokerage or retirement account and your profit shows up here, holding by holding.
        </Notice>
      )}
      {p && p.accounts.length > 0 && <Body p={p} />}
    </>
  );
}

function Body({ p }: { p: Profit }) {
  const [chosen, setChosen] = useRemembered<number[]>("accounts", [], {
    always: true,
    valid: (v) => Array.isArray(v) && v.every((x) => typeof x === "number"),
  });
  const ids = useMemo(() => picked(p, chosen), [p, chosen]);
  const all = ids.size === p.accounts.length;
  const toggle = (id: number) => {
    const now = all ? [] : [...ids];
    const next = now.includes(id) ? now.filter((x) => x !== id) : [...now, id];
    setChosen(next.length === p.accounts.length ? [] : next);
  };
  return (
    <>
      {p.accounts.length > 1 && (
        <div className="-mx-4 mb-4 overflow-x-auto px-4 [scrollbar-width:none] md:mx-0 md:px-0">
          <div className="flex w-max gap-2 md:w-auto md:flex-wrap" role="group" aria-label="Accounts">
            <Chip on={all} onClick={() => setChosen([])}>
              All accounts
            </Chip>
            {p.accounts.map((a) => (
              <Chip key={a.id} on={!all && ids.has(a.id)} onClick={() => toggle(a.id)}>
                {a.name}
              </Chip>
            ))}
          </div>
        </div>
      )}
      <Columns>
        <Column>
          <Unrealized p={p} ids={ids} />
          <Realized p={p} ids={ids} />
        </Column>
        <Column>
          <Holdings p={p} ids={ids} />
        </Column>
      </Columns>
    </>
  );
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={cn(
        "inline-flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3.5 text-sm font-medium transition-colors",
        on ? "border-transparent bg-foreground text-background" : "bg-card text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function Tone({ cents, children, className }: { cents: number; children: React.ReactNode; className?: string }) {
  return <span className={cn(cents >= 0 ? "text-positive" : "text-negative", className)}>{children}</span>;
}

function Tile({ label, cents, hint, wide }: { label: string; cents: number; hint: string; wide?: boolean }) {
  return (
    <div className={cn("min-w-0 rounded-xl bg-muted/50 px-3 py-3 sm:px-4", wide && "col-span-2")}>
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-base font-semibold tracking-tight tabular-nums sm:text-xl">
        <Tone cents={cents}>
          <Money cents={cents} signed />
        </Tone>
      </p>
      <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground sm:text-xs">{hint}</p>
    </div>
  );
}

// ---- not sold yet

function Unrealized({ p, ids }: { p: Profit; ids: Set<number> }) {
  const t = totals(p, ids);
  const taxed = p.accounts.some((a) => ids.has(a.id) && a.taxable);
  const sheltered = p.accounts.some((a) => ids.has(a.id) && !a.taxable);
  const paidShare = t.cost > 0 ? Math.min(100, Math.max(0, (t.cost / Math.max(t.cost + Math.max(t.gain, 0), 1)) * 100)) : 100;
  const tiles = [
    taxed && { label: "Long-term", cents: t.long, hint: "Held over a year: the lower tax rate if sold" },
    taxed && { label: "Short-term", cents: t.short, hint: "Held a year or less: taxed like pay if sold" },
    taxed && t.unknown !== 0 && { label: "Term not known yet", cents: t.unknown, hint: "Bought before the activity on file" },
    sheltered && { label: "Not taxed", cents: t.untaxed, hint: "In retirement and HSA accounts" },
  ].filter(Boolean) as { label: string; cents: number; hint: string }[];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Not sold yet</CardTitle>
        <Badge variant="muted">Unrealized</Badge>
      </CardHeader>
      <CardContent>
        <p className="text-4xl font-semibold tracking-tight tabular-nums">
          <Tone cents={t.gain}>
            <Money cents={t.gain} signed />
          </Tone>
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          {t.percent !== null && (
            <Tone cents={t.gain} className="font-medium">
              {signedPercent(t.percent)}
            </Tone>
          )}{" "}
          on <Money cents={t.cost} /> paid, worth <Money cents={t.value - t.uncosted} /> today
        </p>
        {t.cost > 0 && (
          <div className="mt-4" aria-hidden>
            <div className="flex h-2.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-chart-1/70" style={{ width: `${paidShare}%` }} />
              {t.gain > 0 && <div className="h-full bg-positive" style={{ width: `${100 - paidShare}%` }} />}
            </div>
            <div className="mt-1.5 flex justify-between text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-chart-1/70" /> What you paid
              </span>
              {t.gain > 0 && (
                <span className="flex items-center gap-1.5">
                  <span className="size-2 rounded-full bg-positive" /> Growth
                </span>
              )}
            </div>
          </div>
        )}
        {tiles.length > 0 && (
          <div className="mt-5 grid grid-cols-2 gap-2 sm:gap-3">
            {tiles.map((x, i) => (
              <Tile key={x.label} {...x} wide={tiles.length % 2 === 1 && i === tiles.length - 1} />
            ))}
          </div>
        )}
        <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
          From your tax lots: each buy is a lot, and sales take the oldest shares first, the way brokerages do by default.
          {t.uncosted > 0 && (
            <>
              {" "}
              <Money cents={t.uncosted} /> of holdings have no cost on file, so they&apos;re left out.
            </>
          )}
          {taxed && t.unknown !== 0 && " Shares older than the first activity your brokerage sent count once their buy date is clear."}
        </p>
      </CardContent>
    </Card>
  );
}

// ---- sold

function Realized({ p, ids }: { p: Profit; ids: Set<number> }) {
  const years = p.sales_by_year.map((y) => y.year);
  const [year, setYear] = useState(years[0] ?? new Date().getFullYear());
  const [more, setMore] = useState(false);
  const s = soldIn(p, year, ids);
  const taxed = p.accounts.some((a) => ids.has(a.id) && a.taxable);
  const sheltered = p.accounts.some((a) => ids.has(a.id) && !a.taxable);
  const gain = s.short + s.long + s.untaxed;
  const sales = p.sales.filter((x) => ids.has(x.account_id) && x.date.startsWith(String(year)));
  const shown = more ? sales : sales.slice(0, FIRST_SALES);
  const names = new Map(p.accounts.map((a) => [a.id, a.name]));
  const since = p.accounts
    .filter((a) => ids.has(a.id) && a.history_from)
    .map((a) => a.history_from!)
    .sort()[0];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Sold</CardTitle>
        <Badge variant="muted">Realized</Badge>
      </CardHeader>
      <CardContent>
        {years.length > 1 && (
          <div className="mb-4 grid gap-1 rounded-full bg-muted p-1" style={{ gridTemplateColumns: `repeat(${years.length}, 1fr)` }} role="radiogroup" aria-label="Year">
            {years.map((y) => (
              <button
                key={y}
                type="button"
                role="radio"
                aria-checked={year === y}
                onClick={() => {
                  setYear(y);
                  setMore(false);
                }}
                className={cn(
                  "h-9 rounded-full text-sm font-medium transition-colors",
                  year === y ? "bg-card text-foreground shadow-sm" : "text-muted-foreground",
                )}
              >
                {y === new Date().getFullYear() ? "This year" : y}
              </button>
            ))}
          </div>
        )}
        {s.count === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing sold in {year === new Date().getFullYear() ? "this year" : year}
            {all(p, ids) ? "" : " in these accounts"}.
          </p>
        ) : (
          <>
            <p className="text-3xl font-semibold tracking-tight tabular-nums">
              <Tone cents={gain}>
                <Money cents={gain} signed />
              </Tone>
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              From {s.count} {s.count === 1 ? "sale" : "sales"} bringing in <Money cents={s.proceeds} />
            </p>
            <div className="mt-4 grid grid-cols-2 gap-2 sm:gap-3">
              {taxed && <Tile label="Long-term" cents={s.long} hint="Taxed at the lower rate" />}
              {taxed && <Tile label="Short-term" cents={s.short} hint="Taxed like pay" />}
              {sheltered && s.untaxed !== 0 && (
                <Tile label="Not taxed" cents={s.untaxed} hint="Sold inside retirement and HSA" wide={taxed} />
              )}
            </div>
            <ul className="mt-4 divide-y">
              {shown.map((x, i) => (
                <li key={`${x.date}-${x.symbol}-${x.account_id}-${i}`} className="flex items-center gap-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {x.symbol} <span className="font-normal text-muted-foreground">· {shareCount.format(x.quantity)} shares</span>
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {asOf(x.date)} · {names.get(x.account_id)}
                    </p>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="text-sm font-medium tabular-nums">
                      {x.gain_cents === null ? (
                        <span className="text-muted-foreground">Cost not on file</span>
                      ) : (
                        <Tone cents={x.gain_cents}>
                          <Money cents={x.gain_cents} signed />
                        </Tone>
                      )}
                    </p>
                    <p className="text-xs text-muted-foreground tabular-nums">
                      <Money cents={x.proceeds_cents} /> sold
                    </p>
                  </div>
                </li>
              ))}
            </ul>
            {sales.length > FIRST_SALES && (
              <Button variant="ghost" size="sm" className="mt-1 -ml-2" onClick={() => setMore(!more)} aria-expanded={more}>
                {more ? "Show fewer" : `Show all ${sales.length} sales`}
              </Button>
            )}
          </>
        )}
        <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
          From the buys and sells your brokerages sent{since ? `, back to ${asOf(since)}` : ""}.
          {s.uncosted > 0 && (
            <>
              {" "}
              <Money cents={s.uncosted} /> of sales were of shares bought before that, so their gain isn&apos;t counted.
            </>
          )}{" "}
          Your brokerage&apos;s 1099-B is the final word for taxes.
        </p>
      </CardContent>
    </Card>
  );
}

function all(p: Profit, ids: Set<number>) {
  return ids.size === p.accounts.length;
}

// ---- by holding

type Sort = "gain" | "percent" | "value";
const SORTS: { id: Sort; label: string }[] = [
  { id: "gain", label: "Most gain" },
  { id: "percent", label: "Best percent" },
  { id: "value", label: "Largest" },
];

function Holdings({ p, ids }: { p: Profit; ids: Set<number> }) {
  const [sort, setSort] = useRemembered<Sort>("sort", "gain", { valid: (v) => SORTS.some((s) => s.id === v) });
  const rows = useMemo(() => {
    const pct = (h: { cost_cents: number } & Parameters<typeof gainOf>[0]) => (h.cost_cents > 0 ? gainOf(h) / h.cost_cents : -Infinity);
    const key = (h: ReturnType<typeof byHolding>[number]) =>
      sort === "gain" ? gainOf(h) : sort === "percent" ? pct(h) : h.value_cents;
    return byHolding(p, ids).sort((a, b) => key(b) - key(a));
  }, [p, ids, sort]);
  const most = Math.max(1, ...rows.map((h) => Math.abs(gainOf(h))));
  const names = new Map(p.accounts.map((a) => [a.id, a.name]));
  return (
    <Card>
      <CardHeader>
        <CardTitle>By holding</CardTitle>
        <Select aria-label="Sort holdings" value={sort} onChange={(e) => setSort(e.target.value as Sort)} className="h-8 w-auto py-0 text-sm">
          {SORTS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </Select>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No holdings in these accounts.</p>
        ) : (
          <ul className="-mx-2">
            {rows.map((h) => {
              const g = gainOf(h);
              const pct = h.cost_cents > 0 ? (g / h.cost_cents) * 100 : null;
              const where = h.accounts.length > 1 ? `${h.accounts.length} accounts` : names.get(h.accounts[0]!);
              const inner = (
                <>
                  <div className="min-w-0 flex-1">
                    <p className="flex items-baseline gap-2">
                      <span className="max-w-[9rem] shrink-0 truncate font-semibold">{h.symbol}</span>
                      <span className="truncate text-xs text-muted-foreground">{h.name}</span>
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      Paid <Money cents={h.cost_cents} /> · worth <Money cents={h.value_cents - h.uncosted_value_cents} />
                      {ids.size > 1 && where ? ` · ${where}` : ""}
                    </p>
                    <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted" aria-hidden>
                      <div
                        className={cn("h-full rounded-full", g >= 0 ? "bg-positive" : "bg-negative")}
                        style={{ width: `${(Math.abs(g) / most) * 100}%` }}
                      />
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="text-sm font-semibold tabular-nums">
                      {h.cost_cents > 0 ? (
                        <Tone cents={g}>
                          <Money cents={g} signed />
                        </Tone>
                      ) : (
                        <span className="text-muted-foreground">No cost on file</span>
                      )}
                    </p>
                    {pct !== null && (
                      <p className="text-xs tabular-nums">
                        <Tone cents={g}>{signedPercent(pct)}</Tone>
                      </p>
                    )}
                  </div>
                </>
              );
              return (
                <li key={h.symbol}>
                  {hasDetail(h.symbol) ? (
                    <Link href={holdingHref(h.symbol)} className="group flex items-center gap-3 rounded-lg px-2 py-2.5 hover:bg-muted/40">
                      {inner}
                      <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                    </Link>
                  ) : (
                    <div className="flex items-center gap-3 px-2 py-2.5">{inner}</div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        <p className="mt-3 text-xs text-muted-foreground">Tap a holding to see each of its lots and when they turn long-term.</p>
      </CardContent>
    </Card>
  );
}
