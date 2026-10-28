"use client";

import Link from "next/link";
import { useMemo, useState, useSyncExternalStore } from "react";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileUp,
  Landmark,
  PencilLine,
  Sparkles,
  Sunrise,
} from "lucide-react";
import type { BriefState } from "@/components/brief";
import { Money, formatMoney } from "@/components/money";
import { NetWorthSheet } from "@/components/net-worth-sheet";
import { PriceChart } from "@/components/price-chart";
import { Sparkline } from "@/components/charts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "@/components/ui/toast";
import { UploadSheet } from "@/components/upload-sheet";
import { type UploadDue, dueLabel, shortDate } from "@/lib/accounts";
import { api } from "@/lib/api";
import { type ColorName, type Goal, type GoalsState, monthYear } from "@/lib/goals";
import { type LinkedHousehold, type Maturing, resetHousehold, useApi, useHousehold } from "@/lib/household";
import { GROUP_COLOR, type NetWorth, type NetWorthWindow } from "@/lib/networth";
import { monthOf } from "@/lib/search";
import { cn } from "@/lib/utils";

const longDay = new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric" });
const time = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });
const utcDay = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

/** A budget this close to its limit (or past it) is something to look at. */
const NEAR_LIMIT = 0.9;

const noSubscribe = () => () => {};

function greeting(d: Date) {
  const h = d.getHours();
  return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

/**
 * The top of Home: the day, a greeting, and one line on where things stand. That line is today's morning brief
 * when there is one (its points open below it), otherwise a sentence the app puts together from the numbers.
 */
export function Greeting() {
  // The clock is read in the browser only, so the prerendered page (a plain "Home") matches the first render.
  const day = useSyncExternalStore(noSubscribe, () => longDay.format(new Date()), () => "");
  const hello = useSyncExternalStore(noSubscribe, () => greeting(new Date()), () => "Home");
  const state = useHousehold();
  const h = state.status === "ready" && state.household.linked ? state.household : null;
  const { data: brief } = useApi<BriefState>(h && !h.demo ? "/api/brief" : null);
  const [open, setOpen] = useState(false);
  const b = brief?.today;
  const toggle = () => {
    setOpen((o) => !o);
    if (b && !b.read) api(`/api/brief/${b.id}/read`, { method: "POST" }).catch(() => {});
  };

  return (
    <div className="mb-5 grid gap-2">
      <p className="h-4 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{day}</p>
      <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">{hello}</h1>
      {h && (
        <div className="grid max-w-3xl gap-2">
          <p className="text-[15px] leading-relaxed text-pretty">{b ? b.headline : summary(h)}</p>
          {b ? (
            <>
              <button
                type="button"
                onClick={toggle}
                aria-expanded={open}
                className="-ml-1 flex w-max items-center gap-1.5 rounded-md px-1 text-xs text-muted-foreground hover:text-foreground"
              >
                <Sunrise className="size-3.5 text-primary" aria-hidden />
                <span>Morning brief</span> · {time.format(new Date(b.at))}
                <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} aria-hidden />
              </button>
              {open && (
                <ul className="grid gap-1.5 rounded-xl border border-primary/20 bg-accent/40 p-4">
                  {b.points.map((p, i) => (
                    <li key={i} className="flex gap-2.5 text-sm leading-relaxed">
                      <span className="mt-2 size-1.5 shrink-0 rounded-full bg-primary/60" aria-hidden />
                      <span>{p}</span>
                    </li>
                  ))}
                  {b.written_by !== "app" && (
                    <li className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Sparkles className="size-3.5" aria-hidden /> Worded by {b.written_by}, checked against your numbers
                    </li>
                  )}
                </ul>
              )}
            </>
          ) : (
            !h.demo && <p className="text-xs text-muted-foreground">Synced {time.format(new Date(h.as_of))}</p>
          )}
        </div>
      )}
    </div>
  );
}

/** One plain sentence from the numbers, for days without a brief. */
function summary(h: LinkedHousehold): string {
  const parts: string[] = [];
  const change = h.net_worth.change_today_cents;
  if (h.net_worth.history.length > 1 && change !== 0) {
    parts.push(`Net worth is ${change > 0 ? "up" : "down"} ${formatMoney(Math.abs(change))} today`);
  }
  if (h.safe_to_spend) {
    const days = h.safe_to_spend.days_left;
    parts.push(`${formatMoney(h.safe_to_spend.cents)} is safe to spend over the next ${days} ${days === 1 ? "day" : "days"}`);
  }
  if (parts.length === 0) return "Here's where your money stands.";
  const s = parts.join(", and ");
  return s.charAt(0).toUpperCase() + s.slice(1) + ".";
}

type Item = {
  key: string;
  icon: React.ReactNode;
  tone: "accent" | "warning" | "negative" | "blue";
  title: React.ReactNode;
  detail: React.ReactNode;
  actions: React.ReactNode;
  /** Two buttons: on a phone they go under the words, so the words keep their width. */
  wide?: boolean;
};

const TONE = {
  accent: "bg-accent text-accent-foreground",
  warning: "bg-warning/15 text-warning",
  negative: "bg-negative/12 text-negative",
  blue: "bg-chart-2/15 text-chart-2",
};

/**
 * What needs you, each with the button that deals with it: a bank that stopped syncing, a statement to upload, a CD
 * about to mature, a budget at its limit, categories to check. Says so when there's nothing.
 */
export function NeedsYou({ h, className }: { h: LinkedHousehold; className?: string }) {
  const [uploading, setUploading] = useState<number | null>(null);
  const [later, setLater] = useState<number[]>([]);
  const items: Item[] = [];

  for (const s of h.health?.stale ?? []) {
    items.push({
      key: `stale-${s.id}`,
      icon: <AlertTriangle />,
      tone: "warning",
      title: `${s.institution} stopped syncing`,
      detail: "Reconnect it or upload a file",
      actions: (
        <Button size="sm" variant="secondary" asChild>
          <Link href="/settings/accounts">Fix</Link>
        </Button>
      ),
    });
  }

  const snooze = async (u: UploadDue) => {
    if (h.demo) return toast("Reminders stay put while the app shows sample data.");
    try {
      await api(`/api/accounts/${u.account_id}/reminder/snooze`, { body: { days: 3 } });
      setLater((l) => [...l, u.account_id]);
      toast(`Okay. ${u.name} again in 3 days.`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const upload = (u: UploadDue) => (h.demo ? toast("Uploads are off while the app shows sample data.") : setUploading(u.account_id));
  for (const u of (h.health?.uploads ?? []).filter((u) => !later.includes(u.account_id))) {
    items.push({
      key: `upload-${u.account_id}`,
      icon: u.provider === "manual" ? <PencilLine /> : <FileUp />,
      tone: "accent",
      title: u.provider === "manual" ? `Update ${u.name}` : `Upload ${u.name}`,
      detail: `${dueLabel(u)} · has data to ${shortDate(u.newest)}`,
      wide: true,
      actions: (
        <>
          <Button size="sm" variant="ghost" onClick={() => snooze(u)} aria-label={`Remind me about ${u.name} later`}>
            Later
          </Button>
          {u.provider === "file" ? (
            <Button size="sm" onClick={() => upload(u)}>
              Upload
            </Button>
          ) : (
            <Button size="sm" asChild>
              <Link href={`/settings/accounts?account=${u.account_id}`}>Update</Link>
            </Button>
          )}
        </>
      ),
    });
  }

  for (const m of h.health?.maturing ?? []) {
    items.push({
      key: `cd-${m.account_id}`,
      icon: <Landmark />,
      tone: "blue",
      title: cdTitle(m),
      detail: cdDetail(m),
      actions: (
        <Button size="sm" variant="secondary" asChild>
          <Link href={`/settings/accounts?account=${m.account_id}`} aria-label={`Decide what to do with ${m.name}`}>
            Decide
          </Link>
        </Button>
      ),
    });
  }

  const daysLeft = h.safe_to_spend?.days_left ?? daysLeftInMonth();
  const hot = h.budgets
    .filter((b) => b.kind === "flexible" && b.limit_cents > 0 && b.spent_cents / b.limit_cents >= NEAR_LIMIT)
    .sort((a, b) => b.spent_cents / b.limit_cents - a.spent_cents / a.limit_cents)
    .slice(0, 3);
  for (const b of hot) {
    const left = b.limit_cents - b.spent_cents;
    const over = left < 0;
    items.push({
      key: `budget-${b.category}`,
      icon: <AlertTriangle />,
      tone: over ? "negative" : "warning",
      title: over ? (
        <>
          {b.category} is <Money cents={-left} /> over
        </>
      ) : (
        `${b.category} is at ${Math.floor((b.spent_cents / b.limit_cents) * 100)}%`
      ),
      detail: over ? (
        <>
          <Money cents={b.spent_cents} /> of <Money cents={b.limit_cents} /> this month
        </>
      ) : (
        <>
          <Money cents={left} /> left for {daysLeft} {daysLeft === 1 ? "day" : "days"}
        </>
      ),
      actions: (
        <Button size="sm" variant="secondary" asChild>
          <Link href={b.category_id ? monthOf(b.category_id) : "/budget"} aria-label={`See ${b.category} spending this month`}>
            See
          </Link>
        </Button>
      ),
    });
  }

  const review = h.spending?.needs_review ?? 0;
  if (review > 0) {
    items.push({
      key: "review",
      icon: <Sparkles />,
      tone: "accent",
      title: `${review} ${review === 1 ? "category" : "categories"} to check`,
      detail: "One answer per merchant",
      actions: (
        <Button size="sm" variant="secondary" asChild>
          <Link href="/review">Check</Link>
        </Button>
      ),
    });
  }

  if (items.length === 0) {
    return (
      <div className={cn("flex items-center gap-3 rounded-xl border bg-card px-4 py-3 text-sm", className)}>
        <CheckCircle2 className="size-4 shrink-0 text-positive" aria-hidden />
        <span>
          <span className="font-medium">Nothing needs you today.</span>{" "}
          <span className="text-muted-foreground">Uploads, CDs and budgets are all in order.</span>
        </span>
      </div>
    );
  }

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Needs you · {items.length}</CardTitle>
      </CardHeader>
      <CardContent className="pt-1">
        <ul className="grid grid-cols-1 xl:grid-cols-2 xl:gap-x-8">
          {items.map((it, i) => (
            <li
              key={it.key}
              className={cn(
                "flex flex-wrap items-center gap-x-3 gap-y-2 border-t py-3",
                i === 0 && "border-t-0",
                i === 1 && "xl:border-t-0",
              )}
            >
              <span className={cn("grid size-9 shrink-0 place-items-center rounded-full [&_svg]:size-4", TONE[it.tone])} aria-hidden>
                {it.icon}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium wrap-anywhere">{it.title}</p>
                <p className="text-xs text-muted-foreground">{it.detail}</p>
              </div>
              <div className={cn("flex shrink-0 items-center gap-1", it.wide && "max-sm:w-full max-sm:justify-end")}>{it.actions}</div>
            </li>
          ))}
        </ul>
      </CardContent>
      <UploadSheet
        open={uploading !== null}
        onOpenChange={(o) => !o && setUploading(null)}
        onSaved={() => resetHousehold()}
        accountId={uploading ?? undefined}
      />
    </Card>
  );
}

function cdTitle(m: Maturing) {
  if (m.status === "grace") return `${m.name} matured`;
  if (m.days_left === 0) return `${m.name} matures today`;
  return `${m.name} matures in ${m.days_left} ${m.days_left === 1 ? "day" : "days"}`;
}

function cdDetail(m: Maturing) {
  if (m.status === "grace" && m.grace_ends) {
    return `Grace period ends ${utcDay.format(new Date(m.grace_ends))}: move it or let it renew`;
  }
  const renews = m.auto_renew ? "Renews on its own unless you act" : m.auto_renew === false ? "Pays out to you" : "Renew, move, or let it roll";
  return (
    <>
      {m.value_cents ? (
        <>
          <Money cents={m.value_cents} /> on {utcDay.format(new Date(m.maturity))} ·{" "}
        </>
      ) : null}
      {renews}
    </>
  );
}

function daysLeftInMonth() {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate() - d.getDate() + 1;
}

type Range = Exclude<NetWorthWindow["key"], "today">;
const RANGES: { key: Range; label: string; name: string }[] = [
  { key: "week", label: "1W", name: "the last week" },
  { key: "month", label: "1M", name: "the last month" },
  { key: "3m", label: "3M", name: "the last 3 months" },
  { key: "ytd", label: "YTD", name: "this year" },
  { key: "year", label: "1Y", name: "the last year" },
];

/**
 * Net worth as a chart you can scrub, over a week to a year, with what moved it in that time: markets against
 * saving and spending (the same sums as the Net worth sheet, which the title opens). Until those load, the last
 * 90 days from the household.
 */
export function NetWorthHero({ h, nw, className }: { h: LinkedHousehold; nw: NetWorth | null; className?: string }) {
  const [sheet, setSheet] = useState(false);
  const [range, setRange] = useState<Range>("month");
  const [scrub, setScrub] = useState<[number, number] | null>(null);
  const linked = nw && nw.linked ? nw : null;
  const w = linked?.windows.find((x) => x.key === range) ?? null;

  const points = useMemo<[number, number][]>(() => {
    const history = linked?.history ?? h.net_worth.history;
    const from = w?.since ?? "";
    const rows = history.filter((p) => p.date >= from);
    const out: [number, number][] = rows.map((p) => [Date.parse(p.date + "T12:00:00Z") / 1000, p.cents / 100]);
    // Today's point at live prices, as on the headline.
    if (out.length) out[out.length - 1] = [out[out.length - 1]![0], h.net_worth.cents / 100];
    return out;
  }, [linked, h.net_worth, w]);

  const change = h.net_worth.change_today_cents;
  const up = change >= 0;
  const r = RANGES.find((x) => x.key === range)!;
  const partial = w && w.since !== w.start;
  const shown = scrub ? Math.round(scrub[1] * 100) : h.net_worth.cents;

  return (
    <Card className={cn("overflow-hidden", className)}>
      <CardHeader>
        <CardTitle>
          <button
            type="button"
            onClick={() => setSheet(true)}
            aria-haspopup="dialog"
            aria-label="Net worth: see how it adds up and what moved it"
            className="-m-1 flex items-center gap-0.5 rounded-md p-1 hover:text-foreground"
          >
            Net worth <ChevronRight className="size-4" aria-hidden />
          </button>
        </CardTitle>
        {h.net_worth.history.length > 1 && (
          <Badge variant={up ? "positive" : "negative"}>
            {up ? <ArrowUpRight className="size-3.5" aria-hidden /> : <ArrowDownRight className="size-3.5" aria-hidden />}
            <Money cents={change} signed /> today
          </Badge>
        )}
      </CardHeader>
      <CardContent className="pb-2">
        <p className="text-4xl font-semibold tracking-tight md:text-5xl" aria-live="polite">
          <Money cents={shown} />
        </p>
        <p className="mt-1 h-4 text-xs text-muted-foreground">
          {scrub
            ? utcDay.format(new Date(scrub[0] * 1000))
            : w
              ? (
                  <>
                    <Money cents={w.change_cents} signed className={w.change_cents >= 0 ? "text-positive" : "text-negative"} /> over {r.name}
                    {partial && `, since ${utcDay.format(new Date(w.since))}`}
                  </>
                )
              : !h.demo && `Synced ${time.format(new Date(h.as_of))}`}
        </p>
      </CardContent>
      {linked ? (
        points.length > 1 && (
          <div className="px-2 md:px-4">
            <PriceChart
              points={points}
              intraday={false}
              onScrub={setScrub}
              format={(v) => formatMoney(v * 100)}
              label={`Net worth over ${r.name}. Use the arrow keys to read each day.`}
              className="h-40 md:h-52"
            />
          </div>
        )
      ) : (
        <Sparkline
          points={h.net_worth.history.map((p) => p.cents)}
          label={`Net worth over the last ${h.net_worth.history.length} days.`}
        />
      )}
      {linked && (
        <div className="grid gap-3 px-4 pt-2 pb-4">
          <div role="tablist" aria-label="Chart range" className="flex justify-between gap-1">
            {RANGES.map((x) => (
              <button
                key={x.key}
                role="tab"
                aria-selected={range === x.key}
                onClick={() => setRange(x.key)}
                className={cn(
                  "min-w-11 rounded-full px-3 py-1.5 text-xs font-semibold transition-colors",
                  range === x.key ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted",
                )}
              >
                {x.label}
              </button>
            ))}
          </div>
          {w && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-chart-1" aria-hidden />
                Markets <Money cents={w.market_cents} signed className="font-medium text-foreground" />
              </span>
              <span className="flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-chart-2" aria-hidden />
                Saving and spending <Money cents={w.saving_cents} signed className="font-medium text-foreground" />
              </span>
              {w.unsplit_cents !== 0 && (
                <span className="flex items-center gap-1.5">
                  <span className="size-2 rounded-full bg-chart-5" aria-hidden />
                  Can&apos;t split <Money cents={w.unsplit_cents} signed className="font-medium text-foreground" />
                </span>
              )}
              <button type="button" onClick={() => setSheet(true)} className="ml-auto flex items-center font-medium text-primary">
                How it adds up <ChevronRight className="size-3.5" aria-hidden />
              </button>
            </div>
          )}
        </div>
      )}
      <NetWorthSheet open={sheet} onOpenChange={setSheet} />
    </Card>
  );
}

/** A progress ring: how far along, with the percent in the middle. */
function Ring({ ratio, className }: { ratio: number; className: string }) {
  const r = 26;
  const c = 2 * Math.PI * r;
  const pct = Math.round(Math.min(Math.max(ratio, 0), 1) * 100);
  return (
    <div className="relative size-16 shrink-0" aria-hidden>
      <svg viewBox="0 0 64 64" className="size-full -rotate-90">
        <circle cx="32" cy="32" r={r} fill="none" stroke="var(--muted)" strokeWidth="7" />
        <circle
          cx="32"
          cy="32"
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={`${((c * pct) / 100).toFixed(1)} ${c.toFixed(1)}`}
          className={className}
        />
      </svg>
      <span className="absolute inset-0 grid place-items-center text-sm font-semibold tabular-nums">{pct}%</span>
    </div>
  );
}

/** How far the money you've invested and saved goes toward covering your spending for good (the Plan page's target). */
export function FreedomTile({ h }: { h: LinkedHousehold }) {
  const p = h.projection;
  if (!p?.target_cents || p.start_cents == null) return null;
  const ratio = p.start_cents / p.target_cents;
  const year = p.fire_year?.mid;
  return (
    <Link href="/plan" className="block">
      <Card className="h-full transition-colors hover:bg-muted/40">
        <CardHeader>
          <CardTitle>Financial independence</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Ring ratio={ratio} className="text-primary" />
          <div className="min-w-0">
            <p className="text-sm font-medium">{ratio >= 1 ? "You're there" : year ? `On track for ${year}` : "Not within 30 years"}</p>
            <p className="text-xs text-muted-foreground">
              <Money cents={p.start_cents} style="compact" /> of <Money cents={p.target_cents} style="compact" />
            </p>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

/** A goal's color as a ring (lib/goals COLORS has the bar and tile). */
const RING: Record<ColorName, string> = {
  teal: "text-chart-1",
  blue: "text-chart-2",
  amber: "text-chart-3",
  pink: "text-chart-4",
  slate: "text-chart-5",
};

/** The goal to watch: the highest-priority one still going, with how far along it is. */
export function GoalTile() {
  const { data } = useApi<GoalsState>("/api/goals");
  if (!data) return null;
  const g = pickGoal(data.goals);
  if (!g) {
    return (
      <Link href="/goals" className="block">
        <Card className="h-full transition-colors hover:bg-muted/40">
          <CardHeader>
            <CardTitle>Goals</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm font-medium">Saving for something?</p>
            <p className="mt-1 text-xs text-muted-foreground">Add a goal and track it here.</p>
          </CardContent>
        </Card>
      </Link>
    );
  }
  const ratio = g.target_cents > 0 ? g.saved_cents / g.target_cents : 0;
  return (
    <Link href="/goals" className="block">
      <Card className="h-full transition-colors hover:bg-muted/40">
        <CardHeader>
          <CardTitle className="line-clamp-2">{g.name}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Ring ratio={ratio} className={RING[g.color]} />
          <div className="min-w-0">
            <p className="text-sm font-medium">
              <Money cents={g.saved_cents} style="compact" /> of <Money cents={g.target_cents} style="compact" />
            </p>
            <p className="text-xs text-muted-foreground">{goalLine(g)}</p>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

function pickGoal(goals: Goal[]): Goal | null {
  const going = goals.filter((g) => g.status !== "done" && g.target_cents > 0);
  going.sort((a, b) => a.priority - b.priority || (a.kind === "emergency" ? 1 : 0) - (b.kind === "emergency" ? 1 : 0));
  return going[0] ?? null;
}

function goalLine(g: Goal) {
  if (g.status === "behind") return g.target_date ? `Behind for ${monthYear.format(new Date(g.target_date))}` : "Behind";
  if (g.projected_month) return `${g.status === "on_track" ? "On track for" : "Around"} ${monthYear.format(new Date(g.projected_month))}`;
  return g.status === "on_track" ? "On track" : "Set a monthly amount";
}

/** Today's biggest moves in dollars, each symbol once across accounts. */
export function Movers({ h, className }: { h: LinkedHousehold; className?: string }) {
  const by = new Map<string, { symbol: string; name: string; value: number; moved: number; percent: number }>();
  for (const x of h.holdings) {
    if (!x.change_today_percent || x.asset_class === "Cash") continue;
    const moved = (x.value_cents * x.change_today_percent) / (100 + x.change_today_percent);
    const row = by.get(x.symbol) ?? { symbol: x.symbol, name: x.name, value: 0, moved: 0, percent: x.change_today_percent };
    row.value += x.value_cents;
    row.moved += moved;
    by.set(x.symbol, row);
  }
  const rows = [...by.values()].sort((a, b) => Math.abs(b.moved) - Math.abs(a.moved)).slice(0, 5);
  if (rows.length === 0) return null;
  const top = Math.max(...rows.map((r) => Math.abs(r.moved)));
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Today&apos;s movers</CardTitle>
        <Link href="/portfolio" className="flex items-center text-sm font-medium text-primary">
          Holdings <ChevronRight className="size-4" aria-hidden />
        </Link>
      </CardHeader>
      <CardContent className="pt-1">
        <ul className="grid grid-cols-1">
          {rows.map((r) => {
            const up = r.moved >= 0;
            return (
              <li key={r.symbol} className="border-t first:border-t-0">
                <Link
                  href={`/portfolio/holding?s=${encodeURIComponent(r.symbol)}`}
                  className="-mx-2 flex items-center gap-3 rounded-lg px-2 py-2.5 transition-colors hover:bg-muted/60"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold">{r.symbol}</p>
                    <p className="truncate text-xs text-muted-foreground">{r.name}</p>
                  </div>
                  <span className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-muted sm:block" aria-hidden>
                    <span
                      className={cn("block h-full rounded-full", up ? "bg-positive" : "bg-negative")}
                      style={{ width: `${Math.max((Math.abs(r.moved) / top) * 100, 6)}%` }}
                    />
                  </span>
                  <div className="w-20 shrink-0 text-right">
                    <p className={cn("text-sm font-medium tabular-nums", up ? "text-positive" : "text-negative")}>
                      {up ? "+" : "−"}
                      {Math.abs(r.percent).toFixed(2)}%
                    </p>
                    <p className="text-xs text-muted-foreground">
                      <Money cents={Math.round(r.moved)} signed />
                    </p>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

export type Upcoming = {
  as_of: string;
  days: number;
  events: { date: string; kind: "pay" | "card" | "cd" | "dividend" | "offer"; title: string; detail: string; cents: number | null; href: string | null }[];
};

const weekday = new Intl.DateTimeFormat("en-US", { weekday: "short", timeZone: "UTC" });
const monthShort = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" });

/** The next two weeks: paydays, card payments, CDs, dividends, offers ending (backend upcoming.py). */
export function ComingUp({ data, className }: { data: Upcoming | null; className?: string }) {
  if (!data) return null;
  // This week's days by name (Fri 31), later ones by month (Nov 14).
  const soon = (iso: string) => (Date.parse(iso) - Date.parse(data.as_of)) / 86_400_000 < 7;
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Coming up</CardTitle>
        <span className="text-xs text-muted-foreground">Next {data.days} days</span>
      </CardHeader>
      <CardContent className="pt-1">
        {data.events.length === 0 ? (
          <p className="py-2 text-sm text-muted-foreground">No paydays, payments or dividends due in the next {data.days} days.</p>
        ) : (
          <ul className="grid grid-cols-1">
            {data.events.map((e, i) => {
              const d = new Date(e.date + "T12:00:00Z");
              const row = (
                <>
                  <span className="w-10 shrink-0 text-center leading-tight">
                    <span className="block text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                      {soon(e.date) ? weekday.format(d) : monthShort.format(d)}
                    </span>
                    <span className="text-lg font-semibold tabular-nums">{d.getUTCDate()}</span>
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium wrap-anywhere">{e.title}</span>
                    <span className="block text-xs text-muted-foreground">{e.detail}</span>
                  </span>
                  {e.cents !== null && (
                    <Money cents={e.cents} signed={e.cents > 0} className={cn("text-sm font-medium", e.cents > 0 && "text-positive")} />
                  )}
                </>
              );
              return (
                <li key={`${e.kind}-${e.date}-${i}`} className="border-t first:border-t-0">
                  {e.href ? (
                    <Link href={e.href} className="-mx-2 flex items-center gap-3 rounded-lg px-2 py-2.5 transition-colors hover:bg-muted/60">
                      {row}
                    </Link>
                  ) : (
                    <div className="flex items-center gap-3 py-2.5">{row}</div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/** Where the money sits: what you own by group as one bar, and what you owe, from the Net worth sheet's sums. */
export function WhereItSits({ nw, className }: { nw: NetWorth | null; className?: string }) {
  const [sheet, setSheet] = useState(false);
  if (!nw || !nw.linked) return null;
  const owned = nw.groups.filter((g) => !g.owed && g.cents > 0);
  const owed = nw.groups.filter((g) => g.owed && g.cents !== 0);
  const total = nw.assets_cents;
  if (total <= 0) return null;
  const pct = (c: number) => Math.round((c / total) * 100);
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Where it sits</CardTitle>
        <Link href="/settings/accounts" className="flex items-center text-sm font-medium text-primary">
          Accounts <ChevronRight className="size-4" aria-hidden />
        </Link>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div
          role="img"
          aria-label={owned.map((g) => `${g.label} ${pct(g.cents)}%`).join(", ")}
          className="flex h-3 gap-0.5 overflow-hidden rounded-full bg-muted"
        >
          {owned.map((g) => (
            <div key={g.key} className={GROUP_COLOR[g.key]} style={{ width: `${(g.cents / total) * 100}%` }} />
          ))}
        </div>
        <ul className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
          {[...owned, ...owed].map((g) => (
            <li key={g.key} className="flex items-center gap-2">
              <span className={cn("size-2 shrink-0 rounded-full", g.owed ? "bg-negative" : GROUP_COLOR[g.key])} aria-hidden />
              <span className="min-w-0 flex-1 truncate text-muted-foreground">{g.label}</span>
              <Money cents={g.cents} className="font-medium" />
            </li>
          ))}
        </ul>
        <button type="button" onClick={() => setSheet(true)} className="flex w-max items-center text-xs font-medium text-primary">
          Every account, and what moved it <ChevronRight className="size-3.5" aria-hidden />
        </button>
      </CardContent>
      <NetWorthSheet open={sheet} onOpenChange={setSheet} />
    </Card>
  );
}
