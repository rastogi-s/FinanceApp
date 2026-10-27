"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, ChevronRight, CreditCard, Globe, Search, Sparkles, Store, X } from "lucide-react";
import { CardFace, CardSheet, ValuesSheet, Chip } from "@/components/card-sheets";
import { Money } from "@/components/money";
import { BackLink, Notice } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { type BestRow, type CardRow, type CardsPage, type Lookup, type Missed, type Ranked, daysUntil, shortDay } from "@/lib/cards";
import { useApi } from "@/lib/household";
import { cn } from "@/lib/utils";

export default function CardsPageView() {
  const { data, error } = useApi<CardsPage>("/api/cards");
  const [local, setLocal] = useState<CardsPage | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [values, setValues] = useState(false);
  const s = local ?? data;
  const row = s?.cards.find((c) => c.account_id === open) ?? null;

  return (
    <>
      <div className="mb-5">
        <BackLink href="/spending">Spending</BackLink>
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Credit cards</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Which card to use where, worked out from each card&apos;s rewards and your own purchases.
        </p>
      </div>
      {error && !s && (
        <Notice icon={<CreditCard className="size-5" />} title="Couldn't load your cards">
          {error}. Check that the server is running, then reload the page.
        </Notice>
      )}
      {!s && !error && (
        <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-28 rounded-xl" />
          <Skeleton className="h-48 rounded-xl" />
          <Skeleton className="h-64 rounded-xl" />
        </div>
      )}
      {s && s.cards.length === 0 && (
        <Notice icon={<CreditCard className="size-5" />} title="No credit cards yet">
          Link a card in Settings, Accounts. Once it&apos;s here, pick which card it is and the app works out where to use it.
        </Notice>
      )}
      {s && s.cards.length > 0 && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <StoreLookup hasCards={s.cards.some((c) => c.linked && c.role === "active")} />
          <Wallet s={s} onOpen={setOpen} />
          <BestByCategory s={s} />
          <MissedPoints s={s} />
          <PointValues s={s} onEdit={() => setValues(true)} />
        </div>
      )}
      {s && (
        <>
          <CardSheet row={row} page={s} open={row !== null} onOpenChange={(o) => !o && setOpen(null)} onSaved={setLocal} />
          <ValuesSheet page={s} open={values} onOpenChange={setValues} onSaved={setLocal} />
        </>
      )}
    </>
  );
}

// ---- type a store

function StoreLookup({ hasCards }: { hasCards: boolean }) {
  const [q, setQ] = useState("");
  const [pick, setPick] = useState<string | null>(null);
  const [kind, setKind] = useState<string | null>(null);
  const [result, setResult] = useState<Lookup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const query = q.trim();

  useEffect(() => {
    if (!query) return;
    let live = true;
    const params = new URLSearchParams({ q: query });
    if (pick) params.set("pick", pick);
    if (kind) params.set("kind", kind);
    const t = setTimeout(() => {
      api<Lookup>(`/api/cards/lookup?${params}`).then(
        (r) => {
          if (!live) return;
          setResult(r);
          setError(null);
        },
        (e: Error) => live && setError(e.message),
      );
    }, 200);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [query, pick, kind]);

  const shown = query ? result : null;
  return (
    <Card className="border-primary/25 md:col-span-2">
      <CardHeader>
        <CardTitle>Which card here?</CardTitle>
        <Store className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            type="search"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPick(null);
              setKind(null);
            }}
            placeholder="Type a store: Costco, Delta, Whole Foods"
            aria-label="Store"
            className="pl-10"
            autoComplete="off"
            enterKeyHint="search"
          />
          {q && (
            <button
              type="button"
              onClick={() => setQ("")}
              aria-label="Clear"
              className="absolute top-1/2 right-2 grid size-8 -translate-y-1/2 place-items-center rounded-full text-muted-foreground hover:bg-muted"
            >
              <X className="size-4" aria-hidden />
            </button>
          )}
        </div>
        {!hasCards && (
          <p className="text-sm text-muted-foreground">Pick which card each of your cards is (below) and the best one shows here.</p>
        )}
        {error && query && <p className="text-sm text-negative">{error}</p>}
        {shown && (
          <div aria-live="polite" className="grid grid-cols-1 gap-4">
            {shown.matches.length > 1 && (
              <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Which store">
                {shown.matches.map((m) => (
                  <Chip key={m.id} on={(shown.chosen ?? null) === m.id} onClick={() => setPick(m.id)}>
                    {m.name}
                    {m.count ? <span className="text-muted-foreground"> · {m.count}</span> : null}
                  </Chip>
                ))}
              </div>
            )}
            {!shown.found ? (
              <div>
                <p className="text-sm">
                  The app hasn&apos;t seen <span className="font-medium">{shown.query}</span>. What kind of store is it?
                </p>
                <div className="mt-3 flex flex-wrap gap-2" role="radiogroup" aria-label="Kind of store">
                  {shown.kinds.map((k) => (
                    <Chip key={k.id} on={kind === k.id} onClick={() => setKind(k.id)}>
                      {k.label}
                    </Chip>
                  ))}
                </div>
              </div>
            ) : (
              <LookupResult r={shown} onKind={setKind} />
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function LookupResult({ r, onKind }: { r: Lookup; onKind: (k: string | null) => void }) {
  const [wrong, setWrong] = useState(false);
  const channels = r.channels ?? [];
  const both = channels.length === 2 && !r.same;
  return (
    <div className="grid grid-cols-1 gap-3">
      <div>
        <p className="font-medium">{r.name}</p>
        <p className="text-xs text-muted-foreground">
          {r.kind?.join(", ")} · {r.how}{" "}
          <button type="button" className="font-medium text-primary" onClick={() => setWrong((w) => !w)}>
            Not right?
          </button>
        </p>
        {wrong && (
          <div className="mt-2 flex flex-wrap gap-2" role="radiogroup" aria-label="Kind of store">
            {r.kinds.map((k) => (
              <Chip key={k.id} on={false} onClick={() => onKind(k.id)}>
                {k.label}
              </Chip>
            ))}
          </div>
        )}
      </div>
      <div className={cn("grid grid-cols-1 gap-3", both && "md:grid-cols-2")}>
        {(both ? channels : channels.slice(0, 1)).map((ch) => (
          <div key={ch.channel} className="rounded-2xl border p-4">
            <p className="mb-3 flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              {ch.channel === "online" ? <Globe className="size-3.5" aria-hidden /> : <Store className="size-3.5" aria-hidden />}
              {both
                ? ch.channel === "online"
                  ? "Online"
                  : "In store"
                : channels.length === 2
                  ? "In store and online"
                  : ch.channel === "online"
                    ? "Online"
                    : "In store"}
            </p>
            {ch.cards.length === 0 ? (
              <p className="text-sm text-muted-foreground">No cards picked yet.</p>
            ) : (
              <>
                <Winner r={ch.cards[0]!} />
                {ch.cards.length > 1 && (
                  <ul className="mt-3 grid grid-cols-1 gap-1.5 border-t pt-3 text-sm">
                    {ch.cards.slice(1).map((c) => (
                      <li key={c.account_id} className={cn("flex items-center gap-2", c.blocked && "text-muted-foreground")}>
                        <Dot color={c.color} />
                        <span className="min-w-0 flex-1 truncate">{c.card}</span>
                        <span className="shrink-0 tabular-nums">{c.blocked ? "Not taken" : c.rate}</span>
                        <span className="w-14 shrink-0 text-right tabular-nums">
                          <Money cents={c.value_cents} style="exact" />
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">Dollar amounts are what a $100 purchase earns, at what you say points are worth.</p>
    </div>
  );
}

function Winner({ r }: { r: Ranked }) {
  return (
    <div>
      <div className="flex items-center gap-3">
        <span className="h-7 w-11 shrink-0 rounded-md shadow-sm sm:h-9 sm:w-14" style={{ background: r.color }} aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="leading-snug font-semibold">{r.card}</p>
          <p className="text-xs text-muted-foreground">{r.why}</p>
        </div>
        <div className="text-right">
          <p className="text-xl font-semibold tracking-tight tabular-nums">{r.rate}</p>
          <p className="text-xs text-muted-foreground tabular-nums">
            <Money cents={r.value_cents} style="exact" /> on $100
          </p>
        </div>
      </div>
      {(r.cap || r.portal || r.warnings.length > 0) && (
        <ul className="mt-2.5 grid grid-cols-1 gap-1 text-xs">
          {r.cap && <li className="text-muted-foreground">{r.cap}</li>}
          {r.portal && (
            <li className="flex items-center gap-1 text-primary">
              <Sparkles className="size-3.5" aria-hidden /> {r.portal}
            </li>
          )}
          {r.warnings.map((w) => (
            <li key={w} className="flex items-start gap-1 text-warning">
              <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden /> {w}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Dot({ color }: { color: string }) {
  return <span className="size-2.5 shrink-0 rounded-full" style={{ background: color }} aria-hidden />;
}

// ---- your cards

function Wallet({ s, onOpen }: { s: CardsPage; onOpen: (id: number) => void }) {
  const unlinked = s.cards.filter((c) => !c.linked).length;
  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>Your cards</CardTitle>
        {unlinked > 0 && <Badge variant="warning">{unlinked === 1 ? "1 to set up" : `${unlinked} to set up`}</Badge>}
      </CardHeader>
      <CardContent>
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {s.cards.map((c) => (
            <li key={c.account_id}>
              <WalletCard c={c} asOf={s.as_of} onOpen={() => onOpen(c.account_id)} />
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function WalletCard({ c, asOf, onOpen }: { c: CardRow; asOf: string; onOpen: () => void }) {
  const due = c.statement?.due_date;
  const days = due ? daysUntil(due, asOf) : null;
  return (
    <button type="button" onClick={onOpen} className="group block w-full text-left" aria-label={`${c.label}: details`}>
      <CardFace row={c} />
      <div className="mt-3 grid grid-cols-1 gap-1.5 px-0.5 text-sm">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-muted-foreground">Owed</span>
          <span className="font-medium tabular-nums">{c.balance_cents === null ? "—" : <Money cents={c.balance_cents} />}</span>
        </div>
        {c.utilization !== null && c.limit_cents !== null && (
          <>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted" aria-hidden>
              <div
                className={cn("h-full rounded-full", c.utilization >= 30 ? "bg-warning" : "bg-primary")}
                style={{ width: `${Math.min(c.utilization, 100)}%` }}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {Math.round(c.utilization)}% of <Money cents={c.limit_cents} /> limit
            </p>
          </>
        )}
        {due && c.statement?.minimum_cents !== null && c.statement?.minimum_cents !== undefined && (
          <p className={cn("text-xs", days !== null && days <= 3 ? "font-medium text-warning" : "text-muted-foreground")}>
            <Money cents={c.statement.minimum_cents} style="exact" /> minimum due {shortDay.format(new Date(due))}
            {days !== null && days >= 0 && days <= 7 ? ` (${days === 0 ? "today" : days === 1 ? "tomorrow" : `in ${days} days`})` : ""}
          </p>
        )}
        {c.linked ? (
          <p className="text-xs text-muted-foreground">
            Earned about <Money cents={c.earned_year_cents} /> this year
            {c.role === "drawer" ? " · in a drawer" : ""}
          </p>
        ) : (
          <p className="flex items-center gap-1 text-xs font-medium text-primary">
            Which card is this? <ChevronRight className="size-3.5" aria-hidden />
          </p>
        )}
      </div>
    </button>
  );
}

// ---- best card by category

function BestByCategory({ s }: { s: CardsPage }) {
  const [all, setAll] = useState(false);
  const linked = s.cards.some((c) => c.linked && c.role === "active");
  const used = s.best.filter((b) => b.spent_cents > 0).sort((a, b) => b.spent_cents - a.spent_cents);
  const rest = s.best.filter((b) => b.spent_cents === 0);
  const rows = all || used.length === 0 ? [...used, ...rest] : used;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Best card by category</CardTitle>
      </CardHeader>
      <CardContent>
        {!linked ? (
          <p className="text-sm text-muted-foreground">
            Pick which card each of your cards is, and the best one for every kind of purchase shows here.
          </p>
        ) : (
          <>
            <ul className="grid grid-cols-1 divide-y">
              {rows.map((b) => (
                <BestLine key={b.category} b={b} />
              ))}
            </ul>
            {!all && rest.length > 0 && used.length > 0 && (
              <Button variant="ghost" size="sm" className="mt-2 -ml-2" onClick={() => setAll(true)}>
                {rest.length} more categories <ChevronRight />
              </Button>
            )}
            <p className="mt-3 text-xs text-muted-foreground">
              Ordered by what you spent in the last 12 months, on cards and from the bank.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function BestLine({ b }: { b: BestRow }) {
  return (
    <li className="flex items-center gap-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{b.label}</p>
        <p className="text-xs text-muted-foreground">
          {b.spent_cents > 0 ? (
            <>
              <Money cents={b.spent_cents} /> a year
            </>
          ) : (
            "Nothing yet"
          )}
        </p>
      </div>
      {b.best && (
        <div className="min-w-0 text-right">
          <p className="flex items-center justify-end gap-1.5 text-sm">
            <Dot color={b.best.color} />
            <span className="truncate font-medium">{b.best.product}</span>
            <span className="tabular-nums">{b.best.rate}</span>
          </p>
          {b.next && b.next.value_cents < b.best.value_cents ? (
            <p className="truncate text-xs text-muted-foreground">
              then {b.next.product} {b.next.rate}
            </p>
          ) : b.next ? (
            <p className="truncate text-xs text-muted-foreground">same as {b.next.product}</p>
          ) : null}
          {b.best.cap && <p className="truncate text-xs text-muted-foreground">{b.best.cap}</p>}
        </div>
      )}
    </li>
  );
}

// ---- missed points

const WINDOWS = [1, 3, 6, 12] as const;

function MissedPoints({ s }: { s: CardsPage }) {
  const [months, setMonths] = useState<(typeof WINDOWS)[number]>(12);
  const linked = s.cards.some((c) => c.linked);
  const { data, error } = useApi<Missed>(linked ? `/api/cards/missed?months=${months}` : null);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Points left on the table</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4">
        {!linked ? (
          <p className="text-sm text-muted-foreground">
            Once your cards are set up, this compares what each purchase earned with your best card for it.
          </p>
        ) : (
          <>
            <div role="radiogroup" aria-label="Over" className="grid grid-cols-4 gap-1 rounded-full bg-muted p-1 text-xs sm:text-sm">
              {WINDOWS.map((m) => (
                <button
                  key={m}
                  type="button"
                  role="radio"
                  aria-checked={months === m}
                  onClick={() => setMonths(m)}
                  className={cn(
                    "rounded-full py-1.5 font-medium transition-colors",
                    months === m ? "bg-card shadow-sm" : "text-muted-foreground",
                  )}
                >
                  {m === 12 ? "1 year" : `${m} mo`}
                </button>
              ))}
            </div>
            {error && <p className="text-sm text-negative">{error}</p>}
            {!data || data.months !== months ? <Skeleton className="h-40 rounded-xl" /> : <MissedBody m={data} />}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function MissedBody({ m }: { m: Missed }) {
  if (m.spent_cents === 0) return <p className="text-sm text-muted-foreground">No card purchases in this window.</p>;
  const share = m.best_cents > 0 ? m.earned_cents / m.best_cents : 1;
  return (
    <>
      <div>
        <p className={cn("text-3xl font-semibold tracking-tight", m.missed_cents > 0 ? "text-warning" : "text-positive")}>
          <Money cents={m.missed_cents} />
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          {m.missed_cents > 0 ? (
            <>
              more you could have earned on <Money cents={m.spent_cents} /> of card spending. You earned{" "}
              <Money cents={m.earned_cents} className="text-foreground" /> of a possible{" "}
              <Money cents={m.best_cents} className="text-foreground" />.
            </>
          ) : (
            <>missed. Every purchase went on your best card.</>
          )}
        </p>
        <div
          className="mt-3 h-2 w-full overflow-hidden rounded-full bg-muted"
          role="img"
          aria-label={`Earned ${Math.round(share * 100)}% of what was possible`}
        >
          <div className="h-full rounded-full bg-positive" style={{ width: `${Math.min(share, 1) * 100}%` }} />
        </div>
      </div>
      {m.categories.some((c) => c.missed_cents > 0) && (
        <ul className="grid grid-cols-1 gap-2 text-sm">
          {m.categories
            .filter((c) => c.missed_cents > 0)
            .slice(0, 5)
            .map((c) => (
              <li key={c.category} className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{c.label}</span>
                  {c.use && <span className="block truncate text-xs text-muted-foreground">Use {c.use}</span>}
                </span>
                <span className="shrink-0 font-medium text-warning tabular-nums">
                  <Money cents={c.missed_cents} />
                </span>
              </li>
            ))}
        </ul>
      )}
      {m.purchases.length > 0 && (
        <details className="group text-sm">
          <summary className="cursor-pointer list-none font-medium text-primary">
            Biggest misses <ChevronRight className="inline size-4 transition-transform group-open:rotate-90" aria-hidden />
          </summary>
          <ul className="mt-2 grid grid-cols-1 gap-2">
            {m.purchases.map((p) => (
              <li key={p.id} className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{p.merchant}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {shortDay.format(new Date(p.date))} · <Money cents={p.cents} style="exact" /> on {p.used}, {p.best} earns more
                  </span>
                </span>
                <span className="shrink-0 text-warning tabular-nums">
                  <Money cents={p.missed_cents} style="exact" />
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

// ---- what points are worth

function PointValues({ s, onEdit }: { s: CardsPage; onEdit: () => void }) {
  const programs = new Set(s.cards.filter((c) => c.product).map((c) => c.product!.program));
  const mine = s.programs.filter((p) => programs.has(p.name));
  if (mine.length === 0) return null;
  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>What your points are worth</CardTitle>
        <Button variant="ghost" size="sm" className="-mt-1.5 -mr-2" onClick={onEdit}>
          Change <ChevronRight />
        </Button>
      </CardHeader>
      <CardContent>
        <ul className="flex flex-wrap gap-2 text-sm">
          {mine.map((p) => (
            <li key={p.id} className="rounded-full bg-muted px-3 py-1.5">
              {p.name}: <span className="font-medium tabular-nums">{p.unit === "%" ? "cash" : `${p.cpp}¢ a point`}</span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-muted-foreground">
          Every comparison on this page uses these values. Points you move to airlines or hotels are often worth more than a cent.
        </p>
      </CardContent>
    </Card>
  );
}
