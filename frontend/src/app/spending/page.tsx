"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ArrowLeftRight, Search, SlidersHorizontal, Sparkles, X } from "lucide-react";
import { CategorySheet } from "@/components/category-sheet";
import { Money } from "@/components/money";
import { HouseholdPage } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { type LinkedHousehold, type Transaction, useApi } from "@/lib/household";
import {
  EMPTY,
  type Filters,
  KINDS,
  RANGES,
  type Results,
  cents,
  dates,
  fromParams,
  isEmpty,
  sheetCount,
  toParams,
  toQuery,
} from "@/lib/search";
import { cn } from "@/lib/utils";

type Category = { id: number; name: string; kind: string };

const day = new Intl.DateTimeFormat("en-US", { weekday: "long", month: "short", day: "numeric", timeZone: "UTC" });
const short = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

function byDay(transactions: Transaction[]) {
  const groups = new Map<string, Transaction[]>();
  for (const t of transactions) groups.set(t.date, [...(groups.get(t.date) ?? []), t]);
  return [...groups.entries()];
}

export default function SpendingPage() {
  return (
    <Suspense>
      <HouseholdPage
        title="Spending"
        subtitle="Transfers between your own accounts are left out of every total. Tap one to change its category."
      >
        {(h) => <Spending h={h} />}
      </HouseholdPage>
    </Suspense>
  );
}

/** Fetches a page of matches whenever the filters change; "Show more" adds the next page. */
function useResults(f: Filters) {
  const query = toQuery(f);
  const [pages, setPages] = useState<{ query: string; results: Results } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [more, setMore] = useState(false);

  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      api<Results>(`/api/transactions?${query}`).then(
        (results) => {
          if (!live) return;
          setPages({ query, results });
          setError(null);
        },
        (e: Error) => live && setError(e.message),
      );
    }, 200); // typing in the search box shouldn't send a request per letter
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [query, version]);

  useEffect(() => {
    const reload = () => setVersion((v) => v + 1);
    window.addEventListener("app-refresh", reload);
    return () => window.removeEventListener("app-refresh", reload);
  }, []);

  const loadMore = async () => {
    const r = pages?.results;
    if (!r?.next_offset || more) return;
    setMore(true);
    try {
      const next = await api<Results>(`/api/transactions?${toQuery(f, r.next_offset)}`);
      setPages((p) =>
        p && p.query === query ? { query, results: { ...next, transactions: [...p.results.transactions, ...next.transactions] } } : p,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setMore(false);
    }
  };

  return {
    results: pages?.results ?? null,
    stale: pages !== null && pages.query !== query,
    error,
    more,
    loadMore,
    reload: useCallback(() => setVersion((v) => v + 1), []),
  };
}

function Spending({ h }: { h: LinkedHousehold }) {
  const params = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  const [f, setF] = useState<Filters>(() => fromParams(params));
  const [picking, setPicking] = useState<Transaction | null>(null);
  const [sheet, setSheet] = useState(false);
  const { data: categories } = useApi<Category[]>("/api/categories");
  const { results, stale, error, more, loadMore, reload } = useResults(f);

  // Keep the address in step, so going back, reloading or sharing a link keeps the filters.
  const wanted = toParams(f).toString();
  useEffect(() => {
    const t = setTimeout(() => {
      if (wanted !== new URLSearchParams(window.location.search).toString()) {
        router.replace(wanted ? `${path}?${wanted}` : path, { scroll: false });
      }
    }, 400);
    return () => clearTimeout(t);
  }, [wanted, path, router]);

  const set = (patch: Partial<Filters>) => setF((x) => ({ ...x, ...patch }));
  const review = h.transactions.filter((t) => t.needs_review).length;
  const filtered = !isEmpty(f);

  return (
    <>
      <div className="sticky top-[calc(3.5rem+env(safe-area-inset-top))] z-20 -mx-4 mb-3 bg-background/85 px-4 pt-1 pb-3 backdrop-blur-lg">
        <SearchBar f={f} set={set} count={sheetCount(f)} openSheet={() => setSheet(true)} />
        <ActiveChips f={f} set={set} categories={categories} accounts={results?.accounts} review={review} />
      </div>

      <div className="grid grid-cols-1 gap-4">
        {filtered ? <Summary f={f} r={results} /> : <ThisMonth h={h} />}

        {error && <p className="px-1 text-sm text-negative">{error}</p>}
        {!results && !error && <Skeleton className="h-64 rounded-xl" />}
        {results && results.transactions.length === 0 && (
          <div className="px-1 py-8 text-center">
            <p className="text-sm text-muted-foreground">
              {f.kind === "review" && isEmpty({ ...f, kind: "" }) ? "Nothing left to sort. Nicely done." : "Nothing matches that."}
            </p>
            {filtered && f.kind !== "review" && (
              <Button variant="outline" size="sm" className="mt-3" onClick={() => setF(EMPTY)}>
                Clear search and filters
              </Button>
            )}
          </div>
        )}

        <div className={cn("grid grid-cols-1 gap-4 transition-opacity", stale && "opacity-60")} aria-busy={stale}>
          {results &&
            byDay(results.transactions).map(([date, items]) => (
              <section key={date} aria-label={day.format(new Date(date))}>
                <h2 className="mb-2 px-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                  {day.format(new Date(date))}
                </h2>
                <Card>
                  <ul className="divide-y">
                    {items.map((t) => (
                      <Row key={t.id} t={t} onPick={() => setPicking(t)} />
                    ))}
                  </ul>
                </Card>
              </section>
            ))}
        </div>

        {results?.next_offset != null && (
          <Button variant="outline" className="mx-auto" onClick={loadMore} disabled={more}>
            {more ? "Loading…" : `Show more · ${results.count - results.transactions.length} left`}
          </Button>
        )}
      </div>

      <FilterSheet
        open={sheet}
        onClose={() => setSheet(false)}
        f={f}
        set={set}
        clear={() => setF((x) => ({ ...EMPTY, q: x.q }))}
        categories={categories ?? []}
        results={results}
      />
      <CategorySheet
        transaction={picking}
        onClose={() => {
          setPicking(null);
          reload();
        }}
      />
    </>
  );
}

function SearchBar({
  f,
  set,
  count,
  openSheet,
}: {
  f: Filters;
  set: (p: Partial<Filters>) => void;
  count: number;
  openSheet: () => void;
}) {
  const box = useRef<HTMLInputElement>(null);
  return (
    <div className="flex gap-2">
      <div className="relative flex-1">
        <Search className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          ref={box}
          type="search"
          inputMode="search"
          enterKeyHint="search"
          autoComplete="off"
          spellCheck={false}
          maxLength={100}
          placeholder="Search merchants"
          aria-label="Search transactions"
          value={f.q}
          onChange={(e) => set({ q: e.target.value })}
          onKeyDown={(e) => e.key === "Escape" && set({ q: "" })}
          className="rounded-full pr-10 pl-10 [&::-webkit-search-cancel-button]:hidden"
        />
        {f.q && (
          <button
            type="button"
            onClick={() => {
              set({ q: "" });
              box.current?.focus();
            }}
            className="absolute top-1/2 right-2 grid size-8 -translate-y-1/2 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="Clear search"
          >
            <X className="size-4" aria-hidden />
          </button>
        )}
      </div>
      <button
        type="button"
        onClick={openSheet}
        className={cn(
          "relative grid size-12 shrink-0 place-items-center rounded-full border bg-card transition-colors hover:bg-muted md:size-11",
          count > 0 && "border-transparent bg-foreground text-background hover:bg-foreground/90",
        )}
        aria-label={count ? `Filters, ${count} on` : "Filters"}
      >
        <SlidersHorizontal className="size-4" aria-hidden />
        {count > 0 && (
          <span className="absolute -top-0.5 -right-0.5 grid size-5 place-items-center rounded-full bg-primary text-[0.6875rem] font-semibold text-primary-foreground">
            {count}
          </span>
        )}
      </button>
    </div>
  );
}

/** What's on, each removable with one tap, plus the "needs a category" shortcut. */
function ActiveChips({
  f,
  set,
  categories,
  accounts,
  review,
}: {
  f: Filters;
  set: (p: Partial<Filters>) => void;
  categories: Category[] | null;
  accounts?: { id: number; name: string }[];
  review: number;
}) {
  const chips: { key: string; label: string; off: () => void }[] = [];
  if (f.range) {
    const { start, end } = dates(f);
    const label =
      f.range === "custom"
        ? [start && short.format(new Date(start)), end && short.format(new Date(end))].filter(Boolean).join(" – ") || "Pick dates"
        : RANGES.find((r) => r.id === f.range)!.label;
    chips.push({ key: "range", label, off: () => set({ range: "", start: "", end: "" }) });
  }
  if (f.kind && f.kind !== "review") {
    chips.push({ key: "kind", label: KINDS.find((k) => k.id === f.kind)!.label, off: () => set({ kind: "" }) });
  }
  for (const c of f.categories) {
    const name = c === "none" ? "Uncategorized" : (categories?.find((x) => String(x.id) === c)?.name ?? "Category");
    chips.push({ key: `c${c}`, label: name, off: () => set({ categories: f.categories.filter((x) => x !== c) }) });
  }
  for (const a of f.accounts) {
    const name = accounts?.find((x) => x.id === a)?.name ?? "Account";
    chips.push({ key: `a${a}`, label: name, off: () => set({ accounts: f.accounts.filter((x) => x !== a) }) });
  }
  const lo = cents(f.min);
  const hi = cents(f.max);
  if (lo !== null || hi !== null) {
    const $ = (c: number) => `$${(c / 100).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
    const label = lo !== null && hi !== null ? `${$(lo)} – ${$(hi)}` : lo !== null ? `${$(lo)} or more` : `Up to ${$(hi!)}`;
    chips.push({ key: "amount", label, off: () => set({ min: "", max: "" }) });
  }
  if (!chips.length && !review && f.kind !== "review") return null;

  return (
    <div className="-mx-4 mt-2.5 flex gap-2 overflow-x-auto px-4 [scrollbar-width:none]" role="group" aria-label="Filters on">
      {(review > 0 || f.kind === "review") && (
        <Chip on={f.kind === "review"} onClick={() => set({ kind: f.kind === "review" ? "" : "review" })}>
          <Sparkles className="size-3.5" aria-hidden /> Needs a category{review > 0 && ` · ${review}`}
        </Chip>
      )}
      {chips.map((c) => (
        <button
          key={c.key}
          type="button"
          onClick={c.off}
          className="inline-flex h-9 shrink-0 items-center gap-1 rounded-full bg-foreground pr-2.5 pl-3.5 text-sm font-medium text-background"
          aria-label={`${c.label}. Remove`}
        >
          {c.label} <X className="size-3.5 opacity-70" aria-hidden />
        </button>
      ))}
    </div>
  );
}

function ThisMonth({ h }: { h: LinkedHousehold }) {
  const real = h.transactions.filter((t) => !t.transfer);
  const spent = real.reduce((s, t) => s - t.amount_cents, 0);
  const first = h.transactions[h.transactions.length - 1]?.date;
  const days = first ? Math.round((new Date(h.as_of).getTime() - new Date(first).getTime()) / 86_400_000) : 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{h.spending ? "This month" : days <= 16 ? "Last two weeks" : `Last ${days} days`}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-3xl font-semibold tracking-tight">
          <Money cents={h.spending ? h.spending.month_cents : spent} />
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          {h.spending && h.spending.last_month_same_day_cents > 0 ? (
            <>
              <Money cents={h.spending.last_month_same_day_cents} /> by this day last month
            </>
          ) : (
            <>across {real.length} purchases</>
          )}
        </p>
      </CardContent>
    </Card>
  );
}

/** Totals for everything that matches, not just the rows loaded so far. */
function Summary({ f, r }: { f: Filters; r: Results | null }) {
  if (!r) return <Skeleton className="h-32 rounded-xl" />;
  const span = r.first && r.last && (r.first === r.last ? short.format(new Date(r.first)) : `${short.format(new Date(r.first))} – ${short.format(new Date(r.last))}`);
  const showIn = f.kind !== "out" && r.in_cents > 0;
  const showOut = f.kind !== "in";
  return (
    <Card>
      <CardHeader>
        <CardTitle aria-live="polite">
          {r.count.toLocaleString("en-US")} {r.count === 1 ? "transaction" : "transactions"}
        </CardTitle>
        {span && <span className="text-xs text-muted-foreground">{span}</span>}
      </CardHeader>
      <CardContent className="flex flex-wrap gap-x-8 gap-y-3">
        {f.kind === "transfer" ? (
          <p className="text-sm text-muted-foreground">Transfers between your own accounts aren&apos;t counted as spending or income.</p>
        ) : (
          <>
            {showOut && (
              <div>
                <p className="text-xs text-muted-foreground">Money out</p>
                <p className="text-2xl font-semibold tracking-tight">
                  <Money cents={r.out_cents} style="exact" />
                </p>
              </div>
            )}
            {showIn && (
              <div>
                <p className="text-xs text-muted-foreground">Money in</p>
                <p className="text-2xl font-semibold tracking-tight text-positive">
                  <Money cents={r.in_cents} style="exact" signed />
                </p>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ t, onPick }: { t: Transaction; onPick: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onPick}
        className="flex w-full items-center gap-3 px-5 py-3.5 text-left transition-colors hover:bg-muted/50"
        aria-label={`${t.merchant}, ${t.category}. Change category`}
      >
        <div className="grid size-9 shrink-0 place-items-center rounded-full bg-muted text-sm font-semibold text-muted-foreground" aria-hidden>
          {t.transfer ? <ArrowLeftRight className="size-4" /> : t.merchant[0]}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{t.merchant}</p>
          <p className="truncate text-xs text-muted-foreground">
            {t.account}
            {t.pending && " · pending"}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Money
            cents={t.amount_cents}
            style="exact"
            signed={t.amount_cents > 0}
            className={cn("text-sm", t.transfer ? "text-muted-foreground" : "font-medium", t.amount_cents > 0 && !t.transfer && "text-positive")}
          />
          <Badge variant={t.transfer ? "muted" : t.needs_review ? "warning" : "default"}>{t.category}</Badge>
        </div>
      </button>
    </li>
  );
}

const KIND_ORDER = ["flexible", "fixed", "income", "other", "transfer"];

/** Every filter in one sheet. Changes apply as you tap, so the button can say how many match. */
function FilterSheet({
  open,
  onClose,
  f,
  set,
  clear,
  categories,
  results,
}: {
  open: boolean;
  onClose: () => void;
  f: Filters;
  set: (p: Partial<Filters>) => void;
  clear: () => void;
  categories: Category[];
  results: Results | null;
}) {
  const sorted = useMemo(
    () => [...categories].sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind)),
    [categories],
  );
  const toggle = <T,>(list: T[], x: T) => (list.includes(x) ? list.filter((y) => y !== x) : [...list, x]);
  const badMin = f.min !== "" && cents(f.min) === null;
  const badMax = f.max !== "" && cents(f.max) === null;

  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title="Filters">
      <div className="grid grid-cols-1 gap-6 pb-2">
        <Group label="Show">
          <div className="grid grid-cols-4 gap-1 rounded-full bg-muted p-1" role="radiogroup" aria-label="Show">
            {KINDS.map((k) => (
              <button
                key={k.id}
                type="button"
                role="radio"
                aria-checked={(f.kind === "review" ? "" : f.kind) === k.id}
                onClick={() => set({ kind: k.id })}
                className={cn(
                  "h-9 rounded-full px-1 text-xs font-medium transition-colors sm:text-sm",
                  (f.kind === "review" ? "" : f.kind) === k.id ? "bg-card text-foreground shadow-sm" : "text-muted-foreground",
                )}
              >
                {k.label}
              </button>
            ))}
          </div>
        </Group>

        <Group label="When">
          <div className="flex flex-wrap gap-2">
            {RANGES.map((r) => (
              <Chip key={r.id} on={f.range === r.id} onClick={() => set({ range: r.id })}>
                {r.label}
              </Chip>
            ))}
          </div>
          {f.range === "custom" && (
            <div className="mt-3 grid grid-cols-2 gap-2">
              <label className="grid gap-1 text-xs text-muted-foreground">
                From
                <Input type="date" value={f.start} max={f.end || undefined} onChange={(e) => set({ start: e.target.value })} />
              </label>
              <label className="grid gap-1 text-xs text-muted-foreground">
                To
                <Input type="date" value={f.end} min={f.start || undefined} onChange={(e) => set({ end: e.target.value })} />
              </label>
            </div>
          )}
        </Group>

        <Group label="Amount">
          <div className="grid grid-cols-2 gap-2">
            <Input
              inputMode="decimal"
              autoComplete="off"
              placeholder="Min $"
              aria-label="Smallest amount"
              aria-invalid={badMin || undefined}
              value={f.min}
              onChange={(e) => set({ min: e.target.value.slice(0, 14) })}
            />
            <Input
              inputMode="decimal"
              autoComplete="off"
              placeholder="Max $"
              aria-label="Largest amount"
              aria-invalid={badMax || undefined}
              value={f.max}
              onChange={(e) => set({ max: e.target.value.slice(0, 14) })}
            />
          </div>
          <p className="mt-1.5 text-xs text-muted-foreground">Either direction: a $50 refund counts as $50.</p>
        </Group>

        {results && results.accounts.length > 1 && (
          <Group label="Accounts">
            <div className="flex flex-wrap gap-2">
              {results.accounts.map((a) => (
                <Chip key={a.id} on={f.accounts.includes(a.id)} onClick={() => set({ accounts: toggle(f.accounts, a.id) })}>
                  {a.name}
                </Chip>
              ))}
            </div>
          </Group>
        )}

        {sorted.length > 0 && (
          <Group label="Categories">
            <div className="flex flex-wrap gap-2">
              {sorted
                .filter((c) => c.name !== "Uncategorized")
                .map((c) => (
                  <Chip
                    key={c.id}
                    on={f.categories.includes(String(c.id))}
                    onClick={() => set({ categories: toggle(f.categories, String(c.id)) })}
                  >
                    {c.name}
                  </Chip>
                ))}
              <Chip on={f.categories.includes("none")} onClick={() => set({ categories: toggle(f.categories, "none") })}>
                Uncategorized
              </Chip>
            </div>
          </Group>
        )}

        <div className="sticky bottom-0 -mx-5 flex gap-2 border-t bg-card px-5 pt-3">
          <Button variant="ghost" className="flex-1" onClick={clear} disabled={sheetCount(f) === 0}>
            Clear
          </Button>
          <Button className="flex-[2]" onClick={onClose}>
            {results ? `Show ${results.count.toLocaleString("en-US")}` : "Show"}
          </Button>
        </div>
      </div>
    </Sheet>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section aria-label={label}>
      <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</h3>
      {children}
    </section>
  );
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={cn(
        "inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full border px-3.5 text-sm font-medium transition-colors",
        on ? "border-transparent bg-foreground text-background" : "bg-card text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}
