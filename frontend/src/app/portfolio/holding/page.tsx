"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AlertTriangle, ArrowDownRight, ArrowUpRight, Bell, ChevronRight, Layers, Plus, Sparkles } from "lucide-react";
import { RuleSheet, type RuleIn } from "@/components/alert-sheets";
import { AskAICard, AskAISheet, useResearch } from "@/components/ask-ai";
import { Money } from "@/components/money";
import { BackLink, Notice, Wide } from "@/components/page";
import { PriceChart, pointLabel } from "@/components/price-chart";
import { TaxLotsCard } from "@/components/tax-lots";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { useApi } from "@/lib/household";
import { RANGES, type Range, type Security, asOf, compactNumber, hasDetail, holdingHref, percent, price } from "@/lib/portfolio";
import { cn } from "@/lib/utils";

const SOURCE = { yahoo: "Yahoo Finance", finnhub: "Finnhub", off: "the last sync" } as const;

export default function HoldingPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Holding />
    </Suspense>
  );
}

function Back() {
  return (
    <>
      <Wide />
      <BackLink href="/portfolio">Portfolio</BackLink>
    </>
  );
}

function Loading() {
  return (
    <div aria-busy="true" aria-label="Loading">
      <Back />
      <h1 className="sr-only">Holding</h1>
      <Skeleton className="mb-2 h-9 w-32 rounded-lg" />
      <Skeleton className="mb-5 h-5 w-56 rounded-lg" />
      <Skeleton className="h-72 rounded-xl" />
      <div className="mt-4 grid grid-cols-2 gap-4">
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
      </div>
    </div>
  );
}

function Holding() {
  const symbol = (useSearchParams().get("s") ?? "").toUpperCase();
  const [range, setRange] = useState<Range>("1d");
  const valid = hasDetail(symbol);
  const { data, error, reload } = useApi<Security>(valid ? `/api/securities/${encodeURIComponent(symbol)}?range=${range}` : null);
  const [scrub, setScrub] = useState<[number, number] | null>(null);
  const [alertOpen, setAlertOpen] = useState(false);
  const research = useResearch(valid ? symbol : "");
  const [askOpen, setAskOpen] = useState(false);
  const [askStart, setAskStart] = useState<string | null>(null);
  const openAsk = (question?: string) => {
    setAskStart(question ?? null);
    setAskOpen(true);
  };

  if (!valid || (error && !data)) {
    return (
      <>
        <Back />
        <h1 className="mb-5 text-2xl font-semibold tracking-tight">{symbol || "Holding"}</h1>
        <Notice icon={<AlertTriangle className="size-5" />} title="Couldn't load this holding">
          {valid ? error : "There's no detail page for this one."} Go back to Portfolio and pick it again.
        </Notice>
      </>
    );
  }
  if (!data || data.symbol !== symbol) return <Loading />;

  const loadingRange = data.range !== range;
  const intraday = data.range === "1d" || data.range === "5d";
  const first = data.points[0]?.[1];
  const last = data.points[data.points.length - 1]?.[1];
  // The day's change on 1D; otherwise the change over the range.
  const base = data.range === "1d" ? data.previous_close : first;
  const shown = scrub ? scrub[1] : (data.price ?? last ?? null);
  const change = shown != null && base ? ((shown - base) / base) * 100 : null;
  const rangeName = RANGES.find((r) => r.id === data.range)!.label;

  const saveRule = async (rule: RuleIn) => {
    await api("/api/alerts/rules", { body: { rule } });
    toast("Alert added.");
    reload();
  };

  return (
    <>
      <Back />
      <div className="mb-4 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-3xl font-semibold tracking-tight wrap-anywhere md:text-4xl">{data.symbol}</h1>
          <p className="mt-0.5 truncate text-sm text-muted-foreground">
            {data.name}
            {data.exchange && ` · ${data.exchange}`}
          </p>
        </div>
        <div className="mt-1 flex shrink-0 gap-2">
          <Button size="sm" onClick={() => openAsk()}>
            <Sparkles /> Ask AI
          </Button>
          <Button variant="outline" size="sm" onClick={() => setAlertOpen(true)}>
            <Bell /> Alert
          </Button>
        </div>
      </div>

      <Card className="overflow-hidden">
        <CardContent className="pt-5">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <p className="text-3xl font-semibold tracking-tight tabular-nums" aria-live="polite">
              {shown != null ? price.format(shown) : "No price"}
            </p>
            {change != null && (
              <Badge variant={change >= 0 ? "positive" : "negative"}>
                {change >= 0 ? <ArrowUpRight className="size-3.5" aria-hidden /> : <ArrowDownRight className="size-3.5" aria-hidden />}
                {change >= 0 ? "+" : ""}
                {change.toFixed(2)}% {scrub ? "" : data.range === "1d" ? "today" : `in ${rangeName}`}
              </Badge>
            )}
          </div>
          <p className="mt-1 h-4 text-xs text-muted-foreground">
            {scrub
              ? pointLabel(scrub[0], intraday)
              : data.market_data
                ? data.price_at
                  ? `${SOURCE[data.sources.quotes]} · ${pointLabel(data.price_at, true)}`
                  : SOURCE[data.sources.quotes]
                : "Market data isn't reachable right now; this is the price from the last sync."}
          </p>
        </CardContent>
        <div className={cn("px-2 transition-opacity md:px-4", loadingRange && "opacity-40")}>
          <PriceChart
            points={data.points}
            baseline={data.range === "1d" ? data.previous_close : null}
            intraday={intraday}
            onScrub={setScrub}
            label={`${data.symbol} price over ${rangeName}. Use the arrow keys to read prices.`}
          />
        </div>
        <div role="tablist" aria-label="Chart range" className="flex justify-between gap-1 px-4 pt-2 pb-4">
          {RANGES.map((r) => (
            <button
              key={r.id}
              role="tab"
              aria-selected={range === r.id}
              onClick={() => setRange(r.id)}
              className={cn(
                "min-w-11 rounded-full px-3 py-1.5 text-xs font-semibold transition-colors",
                range === r.id ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted",
              )}
            >
              {r.label}
            </button>
          ))}
        </div>
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
        <AskAICard symbol={data.symbol} r={research} open={openAsk} />
        {data.position && <PositionCard s={data} />}
        <StatsCard s={data} />
        {data.position?.lots && <TaxLotsCard lots={data.position.lots} />}
        {data.exposure && data.exposure.sources.length > 1 && <ExposureCard s={data} />}
        {data.is_fund && <FundCard s={data} />}
        <AlertsCard s={data} onAdd={() => setAlertOpen(true)} />
      </div>

      <AskAISheet symbol={data.symbol} name={data.name} r={research} open={askOpen} onOpenChange={setAskOpen} start={askStart} />
      <RuleSheet open={alertOpen} onOpenChange={setAlertOpen} symbol={data.symbol} currentPrice={data.price} save={saveRule} />
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-2.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium tabular-nums">{children}</dd>
    </div>
  );
}

const shares = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 4 });

function PositionCard({ s }: { s: Security }) {
  const p = s.position!;
  const gainUp = (p.gain_cents ?? 0) >= 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Your position</CardTitle>
        {p.percent_of_portfolio != null && <Badge variant="muted">{percent(p.percent_of_portfolio)} of portfolio</Badge>}
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold tracking-tight">
          <Money cents={p.value_cents} />
        </p>
        {p.gain_cents != null && (
          <p className={cn("mt-0.5 text-sm font-medium", gainUp ? "text-positive" : "text-negative")}>
            <Money cents={p.gain_cents} signed /> ({gainUp ? "+" : ""}
            {p.gain_percent?.toFixed(1)}%) since you bought
          </p>
        )}
        <dl className="mt-3 divide-y">
          <Row label="Shares">
            <span className="money">{shares(p.quantity)}</span>
          </Row>
          {p.cost_basis_cents != null && p.quantity > 0 && (
            <Row label="Average cost">
              <Money cents={Math.round(p.cost_basis_cents / p.quantity)} style="exact" />
            </Row>
          )}
          {p.accounts.map((a) => (
            <Row key={a.account} label={a.account}>
              <Money cents={a.value_cents} />
            </Row>
          ))}
        </dl>
        {p.unvested && (
          <div className="mt-3 rounded-lg bg-muted/60 px-3 py-2.5 text-sm">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-muted-foreground">Not vested yet</span>
              <span className="font-medium tabular-nums">
                {shares(p.unvested.quantity)} sh · <Money cents={p.unvested.value_cents} />
              </span>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              Unvested shares are left out of this position, your portfolio and net worth until they vest.
            </p>
          </div>
        )}
        {p.as_of && <p className="mt-2 text-xs text-muted-foreground">Shares as of the sync on {asOf(p.as_of)}.</p>}
      </CardContent>
    </Card>
  );
}

function Range52({ low, high, now }: { low: number; high: number; now: number | null }) {
  const at = now != null && high > low ? Math.max(0, Math.min(1, (now - low) / (high - low))) : null;
  return (
    <div className="py-2.5 text-sm">
      <div className="flex justify-between">
        <span className="text-muted-foreground">52-week range</span>
        <span className="font-medium tabular-nums">
          {price.format(low)} – {price.format(high)}
        </span>
      </div>
      {at != null && (
        <div className="relative mt-2 h-1.5 rounded-full bg-muted" aria-hidden>
          <span className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-primary" style={{ left: `${at * 100}%` }} />
        </div>
      )}
    </div>
  );
}

function StatsCard({ s }: { s: Security }) {
  const t = s.stats;
  const rows: [string, string][] = [];
  if (t.day_low && t.day_high) rows.push(["Today's range", `${price.format(t.day_low)} – ${price.format(t.day_high)}`]);
  if (s.previous_close) rows.push(["Previous close", price.format(s.previous_close)]);
  if (t.volume) rows.push(["Volume", compactNumber(t.volume)]);
  for (const [days, avg] of [
    [50, t.average_50],
    [200, t.average_200],
  ] as const) {
    if (!avg) continue;
    const vs = s.price ? ((s.price - avg) / avg) * 100 : null;
    rows.push([`${days}-day average`, `${price.format(avg)}${vs == null ? "" : ` · now ${Math.abs(vs).toFixed(1)}% ${vs >= 0 ? "above" : "under"}`}`]);
  }
  if (t.market_cap) rows.push(["Market value", `$${compactNumber(t.market_cap)}`]);
  if (t.pe) rows.push(["P/E (trailing)", t.pe.toFixed(1)]);
  if (t.eps) rows.push(["Earnings a share", price.format(t.eps)]);
  if (t.dividend_yield) rows.push([s.is_fund ? "Yield (last 12 months)" : "Dividend yield", percent(t.dividend_yield, 2)]);
  if (t.beta) rows.push(["Beta", t.beta.toFixed(2)]);
  if (t.industry) rows.push(["Industry", t.industry]);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Key numbers</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 && !t.year_low ? (
          <p className="text-sm text-muted-foreground">No market numbers for this one right now.</p>
        ) : (
          <div className="divide-y">
            {t.year_low && t.year_high ? <Range52 low={t.year_low} high={t.year_high} now={s.price} /> : null}
            <dl className="divide-y">
            {rows.map(([k, v]) => (
              <Row key={k} label={k}>
                {v}
              </Row>
            ))}
            </dl>
          </div>
        )}
        {!s.is_fund && !s.sources.fundamentals && (
          <p className="mt-2 text-xs text-muted-foreground">
            P/E, market value and industry show up once a free Finnhub key is added on the server (FINNHUB_API_KEY).
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function SourceBar({ sources, total }: { sources: { symbol: string; percent: number }[]; total: number }) {
  const colors = ["bg-chart-1", "bg-chart-2", "bg-chart-3", "bg-chart-4", "bg-chart-5"];
  return (
    <>
      <div className="flex h-2.5 w-full gap-0.5 overflow-hidden rounded-full bg-muted" aria-hidden>
        {sources.map((x, i) => (
          <div key={x.symbol} className={colors[i % colors.length]} style={{ width: `${(x.percent / total) * 100}%` }} />
        ))}
      </div>
      <ul className="mt-3 grid grid-cols-1 gap-2">
        {sources.map((x, i) => (
          <li key={x.symbol} className="flex items-center gap-2.5 text-sm">
            <span className={cn("size-2.5 rounded-full", colors[i % colors.length])} aria-hidden />
            <span className="flex-1">{x.symbol === "direct" ? "Held directly" : `Inside ${x.symbol}`}</span>
            <span className="text-muted-foreground tabular-nums">{percent(x.percent, 2)}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

function ExposureCard({ s }: { s: Security }) {
  const e = s.exposure!;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Your real share</CardTitle>
        <Layers className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold tracking-tight">{percent(e.percent, 2)}</p>
        <p className="mb-4 text-sm text-muted-foreground">of everything invested, counting what your funds hold.</p>
        <SourceBar sources={e.sources} total={e.percent} />
      </CardContent>
    </Card>
  );
}

function FundCard({ s }: { s: Security }) {
  const f = s.fund;
  if (!f || f.status !== "ok") {
    return (
      <Card>
        <CardHeader>
          <CardTitle>What&apos;s inside</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            {!s.sources.lookthrough
              ? "Fund holdings aren't being looked up (see LOOKTHROUGH_ENABLED on the server)."
              : f?.status === "not_found"
                ? (f.error ?? "The SEC has no holdings filing for this fund.")
                : f?.status === "error"
                  ? "The fund's filing couldn't be read this time. The app tries again later."
                  : "Looking up this fund's holdings at the SEC. It can take a few minutes the first time."}
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>What&apos;s inside</CardTitle>
        <span className="text-xs text-muted-foreground">
          {f.holdings.toLocaleString()} holdings{f.as_of && ` · as of ${asOf(f.as_of)}`}
        </span>
      </CardHeader>
      <CardContent>
        {f.proxy_of && (
          <p className="mb-3 text-xs text-muted-foreground">
            This fund files no holdings report, so these are from {f.proxy_of}, which tracks the same index.
          </p>
        )}
        {f.equity_percent != null && f.equity_percent < 5 ? (
          <p className="text-sm text-muted-foreground">
            Almost no stocks: {percent(f.equity_percent)} of the fund. It holds bonds or cash, so it adds nothing to any company&apos;s share.
          </p>
        ) : (
          <ol className="grid grid-cols-1 gap-x-8 md:grid-cols-2">
            {f.top.map((c, i) => {
              const inner = (
                <>
                  <span className="w-5 text-xs text-muted-foreground tabular-nums">{i + 1}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium">{c.symbol ?? c.name}</span>
                    {c.symbol && <span className="block truncate text-xs text-muted-foreground">{c.name}</span>}
                  </span>
                  <span className="text-sm tabular-nums">{percent(c.weight_percent, 2)}</span>
                  {hasDetail(c.symbol) && <ChevronRight className="size-4 text-muted-foreground" aria-hidden />}
                </>
              );
              return (
                <li key={`${c.symbol}-${c.name}`} className="border-b last:border-b-0 md:[&:nth-last-child(2)]:border-b-0">
                  {hasDetail(c.symbol) ? (
                    <Link href={holdingHref(c.symbol!)} className="-mx-2 flex items-center gap-3 rounded-lg px-2 py-2.5 hover:bg-muted/50">
                      {inner}
                    </Link>
                  ) : (
                    <div className="flex items-center gap-3 py-2.5">{inner}</div>
                  )}
                </li>
              );
            })}
          </ol>
        )}
        {f.equity_percent != null && f.equity_percent >= 5 && f.equity_percent < 95 && (
          <p className="mt-3 text-xs text-muted-foreground">Stocks are {percent(f.equity_percent)} of this fund; the rest is bonds, cash and other holdings.</p>
        )}
      </CardContent>
    </Card>
  );
}

function AlertsCard({ s, onAdd }: { s: Security; onAdd: () => void }) {
  return (
    <Card className={s.is_fund && s.fund?.status === "ok" ? "md:col-span-2" : undefined}>
      <CardHeader>
        <CardTitle>Price alerts</CardTitle>
        <Link href="/settings/alerts" className="flex items-center text-sm font-medium text-primary">
          All alerts <ChevronRight className="size-4" aria-hidden />
        </Link>
      </CardHeader>
      <CardContent>
        {s.rules.length > 0 && (
          <ul className="mb-3 divide-y">
            {s.rules.map((r) => (
              <li key={r.id} className="flex items-center gap-3 py-2.5 text-sm">
                <Bell className={cn("size-4 shrink-0", r.enabled ? "text-primary" : "text-muted-foreground")} aria-hidden />
                <span className={cn("min-w-0 flex-1 wrap-anywhere", !r.enabled && "text-muted-foreground line-through")}>{r.summary}</span>
                {r.triggered && <Badge variant="warning">Triggered</Badge>}
              </li>
            ))}
          </ul>
        )}
        <Button variant="secondary" size="sm" onClick={onAdd}>
          <Plus /> {s.rules.length ? "Another alert" : `Tell me when ${s.symbol} moves`}
        </Button>
      </CardContent>
    </Card>
  );
}
