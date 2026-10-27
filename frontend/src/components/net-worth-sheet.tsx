"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowDownRight, ArrowUpRight, ChevronRight, Info, Lock } from "lucide-react";
import { Money, formatMoney } from "@/components/money";
import { Skeleton } from "@/components/ui/skeleton";
import { Sheet } from "@/components/ui/sheet";
import { useApi } from "@/lib/household";
import { GROUP_COLOR, type NetWorth, type NetWorthGroup, type NetWorthWindow, type WindowAccount } from "@/lib/networth";
import { cn } from "@/lib/utils";

const shortDay = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const longDay = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

type Linked = Extract<NetWorth, { linked: true }>;

/**
 * Behind Home's Net worth card: how today's figure adds up (every account, grouped, assets minus what's owed), and
 * what moved it over a window, account by account, with markets kept apart from saving and spending.
 */
export function NetWorthSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { data, error } = useApi<NetWorth>(open ? "/api/net-worth" : null);
  const [view, setView] = useState<"math" | "moved">("math");
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Net worth" description="How it adds up, and what moved it.">
      {error ? (
        <p className="text-sm text-negative">{error}</p>
      ) : !data ? (
        <div className="grid gap-3" aria-busy>
          <Skeleton className="h-10 w-48" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : !data.linked ? (
        <p className="text-sm text-muted-foreground">Link an account and its balance shows here.</p>
      ) : (
        <div className="grid grid-cols-1 gap-5">
          <div role="radiogroup" aria-label="Show" className="grid grid-cols-2 gap-1 rounded-full bg-muted p-1 text-sm">
            {(
              [
                ["math", "How it adds up"],
                ["moved", "What moved it"],
              ] as const
            ).map(([v, label]) => (
              <button
                key={v}
                type="button"
                role="radio"
                aria-checked={view === v}
                onClick={() => setView(v)}
                className={cn("rounded-full py-2 font-medium transition-colors", view === v ? "bg-card shadow-sm" : "text-muted-foreground")}
              >
                {label}
              </button>
            ))}
          </div>
          {view === "math" ? <AddsUp n={data} /> : <Moved n={data} />}
        </div>
      )}
    </Sheet>
  );
}

// ---- how it adds up

function AddsUp({ n }: { n: Linked }) {
  const assets = n.groups.filter((g) => !g.owed);
  const owed = n.groups.filter((g) => g.owed);
  return (
    <>
      <div>
        <p className="text-4xl font-semibold tracking-tight">
          <Money cents={n.cents} />
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          <Money cents={n.assets_cents} className="font-medium text-foreground" /> you own, less{" "}
          <Money cents={n.owed_cents} className="font-medium text-foreground" /> you owe.
        </p>
        <Mix groups={assets} total={n.assets_cents} />
      </div>

      <Section title="What you own" total={n.assets_cents}>
        {assets.map((g) => (
          <GroupRow key={g.key} g={g} />
        ))}
      </Section>
      {owed.length > 0 && (
        <Section title="What you owe" total={-n.owed_cents}>
          {owed.map((g) => (
            <GroupRow key={g.key} g={g} />
          ))}
        </Section>
      )}
      <div className="flex items-baseline justify-between rounded-xl bg-muted px-4 py-3 text-sm font-semibold">
        <span>Net worth</span>
        <Money cents={n.cents} className={n.cents < 0 ? "text-negative" : undefined} />
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Fact
          label="Within reach"
          cents={n.within_reach_cents}
          about="Cash and brokerage investments, less what's on your cards: money you could get to in days without touching retirement savings."
        />
        <Fact
          label="Saved for later"
          cents={n.groups.find((g) => g.key === "retirement")?.cents ?? 0}
          about="401(k), IRA and HSA money: growing tax-advantaged, with a penalty to take most of it out early."
        />
      </div>

      {n.unvested.holdings.length > 0 && <Unvested u={n.unvested} />}

      <p className="text-xs text-muted-foreground">
        Each account&apos;s latest balance, with investments at today&apos;s prices, the same as Home. Accounts turned off with{" "}
        <span className="font-medium">Count in net worth</span> in{" "}
        <Link href="/settings/accounts" className="font-medium text-primary underline-offset-4 hover:underline">
          Settings → Accounts
        </Link>{" "}
        aren&apos;t in it.
      </p>
    </>
  );
}

/** One bar of what you own, by group, with each group's share. */
function Mix({ groups, total }: { groups: NetWorthGroup[]; total: number }) {
  const parts = groups.filter((g) => g.cents > 0);
  if (total <= 0 || parts.length === 0) return null;
  const pct = (c: number) => Math.round((c / total) * 100);
  return (
    <div className="mt-3">
      <div role="img" aria-label={parts.map((g) => `${g.label} ${pct(g.cents)}%`).join(", ")} className="flex h-2.5 overflow-hidden rounded-full bg-muted">
        {parts.map((g) => (
          <div key={g.key} className={GROUP_COLOR[g.key]} style={{ width: `${(g.cents / total) * 100}%` }} />
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-hidden>
        {parts.map((g) => (
          <li key={g.key} className="flex items-center gap-1.5">
            <span className={cn("size-2 rounded-full", GROUP_COLOR[g.key])} />
            {g.label} {pct(g.cents)}%
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A group's total; opens to show each account in it. */
function GroupRow({ g }: { g: NetWorthGroup }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="py-1 text-sm">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 rounded-lg py-1.5 text-left">
        <ChevronRight className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")} aria-hidden />
        <span className={cn("size-2 shrink-0 rounded-full", GROUP_COLOR[g.key])} aria-hidden />
        <span className="min-w-0 flex-1">
          {g.label}
          <span className="ml-1.5 text-xs text-muted-foreground">
            {g.accounts.length} account{g.accounts.length === 1 ? "" : "s"}
          </span>
        </span>
        <Money cents={g.cents} className="font-medium tabular-nums" />
      </button>
      {open && (
        <div className="mb-1 ml-6 border-l pl-3">
          <p className="mb-1.5 text-xs text-muted-foreground">{g.about}</p>
          <ul className="grid grid-cols-1 gap-1.5">
            {g.accounts.map((a) => (
              <li key={a.id} className="flex items-baseline justify-between gap-3 text-xs">
                <span className="min-w-0 wrap-anywhere text-muted-foreground">
                  {a.name}
                  {g.key !== "cards" && g.key !== "loans" && <span className="ml-1">· {a.kind}</span>}
                  {a.stale && a.as_of && <span className="ml-1 text-warning">· as of {shortDay.format(new Date(a.as_of))}</span>}
                </span>
                <Money cents={a.cents} className="tabular-nums" />
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

function Fact({ label, cents, about }: { label: string; cents: number; about: string }) {
  return (
    <div className="rounded-xl border px-4 py-3">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
      <p className="mt-0.5 text-xl font-semibold tracking-tight">
        <Money cents={cents} />
      </p>
      <p className="mt-1 text-xs text-muted-foreground">{about}</p>
    </div>
  );
}

function Unvested({ u }: { u: Linked["unvested"] }) {
  return (
    <div className="flex gap-3 rounded-xl bg-muted px-4 py-3 text-sm">
      <Lock className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0">
        <p>
          <span className="font-medium">
            <Money cents={u.cents} /> in stock not vested yet
          </span>{" "}
          <span className="text-muted-foreground">isn&apos;t counted until it vests, since it can still be forfeited.</span>
        </p>
        <ul className="mt-1.5 grid gap-1 text-xs text-muted-foreground">
          {u.holdings.map((h) => (
            <li key={`${h.account}-${h.symbol}`} className="flex justify-between gap-3">
              <span className="min-w-0 wrap-anywhere">
                {h.shares.toLocaleString("en-US", { maximumFractionDigits: 3 })} {h.symbol} in {h.account}
              </span>
              <Money cents={h.value_cents} />
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

// ---- what moved it

function Moved({ n }: { n: Linked }) {
  const [key, setKey] = useState<NetWorthWindow["key"]>("month");
  const w = n.windows.find((x) => x.key === key) ?? n.windows[0]!;
  const up = w.change_cents >= 0;
  const since = new Date(w.since);
  const partial = w.since !== w.start;
  const points = n.history.filter((p) => p.date >= w.since).map((p) => p.cents);
  return (
    <>
      <div role="radiogroup" aria-label="Over" className="grid grid-cols-6 gap-1 rounded-full bg-muted p-1 text-xs sm:text-sm">
        {n.windows.map((x) => (
          <button
            key={x.key}
            type="button"
            role="radio"
            aria-checked={x.key === w.key}
            onClick={() => setKey(x.key)}
            className={cn("rounded-full py-1.5 font-medium transition-colors", x.key === w.key ? "bg-card shadow-sm" : "text-muted-foreground")}
          >
            {x.label}
          </button>
        ))}
      </div>

      <div>
        <p className={cn("flex items-center gap-1 text-3xl font-semibold tracking-tight", up ? "text-positive" : "text-negative")}>
          {up ? <ArrowUpRight className="size-7" aria-hidden /> : <ArrowDownRight className="size-7" aria-hidden />}
          <Money cents={w.change_cents} signed />
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          {w.key === "today" ? "Since yesterday's close" : `Since ${longDay.format(since)}`}
          {partial && w.key !== "today" && ", when your history starts"}, from{" "}
          <Money cents={n.cents - w.change_cents} className="font-medium text-foreground" /> to{" "}
          <Money cents={n.cents} className="font-medium text-foreground" />.
        </p>
      </div>

      <Split w={w} />

      {w.key !== "today" && points.length > 2 && <WindowChart points={points} from={w.since} up={up} />}

      <Section title="By account">
        {w.accounts.length === 0 ? (
          <li className="py-2.5 text-sm text-muted-foreground">No account moved.</li>
        ) : (
          w.accounts.map((a) => <MoverRow key={a.id} a={a} widest={Math.abs(w.accounts[0]!.change_cents)} />)
        )}
      </Section>

      {w.movers.length > 0 && (
        <Section title="Biggest market moves">
          {w.movers.map((m) => (
            <li key={m.symbol}>
              <Link
                href={`/portfolio/holding?s=${encodeURIComponent(m.symbol)}`}
                className="flex items-baseline justify-between gap-3 py-2.5 text-sm"
              >
                <span className="min-w-0 truncate">
                  <span className="font-medium">{m.symbol}</span> <span className="text-muted-foreground">{m.name}</span>
                </span>
                <Money cents={m.cents} signed className={cn("tabular-nums font-medium", m.cents >= 0 ? "text-positive" : "text-negative")} />
              </Link>
            </li>
          ))}
        </Section>
      )}

      {(w.new_accounts.length > 0 || w.unsplit_cents !== 0) && (
        <div className="flex gap-3 rounded-xl bg-muted px-4 py-3 text-sm text-muted-foreground">
          <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
          <div className="grid gap-1.5">
            {w.new_accounts.length > 0 && (
              <p>
                {list(w.new_accounts)} {w.new_accounts.length === 1 ? "was" : "were"} linked during this time, so{" "}
                {w.new_accounts.length === 1 ? "it's" : "they're"} counted from the first balance, not as a jump in net worth.
              </p>
            )}
            {w.unsplit_cents !== 0 && (
              <p>
                <Money cents={w.unsplit_cents} signed className="font-medium text-foreground" /> is from investment accounts that
                don&apos;t send their holdings or activity, so markets and new money can&apos;t be told apart there.
              </p>
            )}
          </div>
        </div>
      )}

      <details className="rounded-xl border px-4 py-3 text-sm">
        <summary className="cursor-pointer font-medium select-none">How this is worked out</summary>
        <ul className="mt-2 grid list-disc gap-1.5 pl-5 text-muted-foreground">
          <li>Each account&apos;s balance now, less its balance at the end of the first day. A card or loan going down counts as up.</li>
          <li>
            <span className="font-medium text-foreground">Markets</span> is what the shares you held on the first day gained or lost as
            prices moved. Shares bought, sold or vested since then aren&apos;t in it.
          </li>
          <li>
            <span className="font-medium text-foreground">Saving and spending</span> is the rest: pay coming in, spending, card balances,
            401(k) contributions, vests and dividends. Money moved between two of your own accounts cancels out.
          </li>
          <li>Balances update when your accounts sync, and investments follow live prices during the day.</li>
        </ul>
      </details>
    </>
  );
}

/** Markets against saving and spending: two numbers that add up to the change. */
function Split({ w }: { w: NetWorthWindow }) {
  const parts = [
    { label: "Markets", cents: w.market_cents, about: "prices of what you held" },
    { label: "Saving and spending", cents: w.saving_cents, about: "money in, out and owed" },
    ...(w.unsplit_cents ? [{ label: "Can't split", cents: w.unsplit_cents, about: "no holdings detail" }] : []),
  ];
  return (
    <div className={cn("grid gap-2", parts.length === 3 ? "grid-cols-3" : "grid-cols-2")}>
      {parts.map((p) => (
        <div key={p.label} className="rounded-xl border px-3 py-2.5">
          <p className="text-xs text-muted-foreground">{p.label}</p>
          <p className={cn("text-lg font-semibold tracking-tight", p.cents > 0 ? "text-positive" : p.cents < 0 ? "text-negative" : undefined)}>
            <Money cents={p.cents} signed />
          </p>
          <p className="text-[11px] leading-tight text-muted-foreground">{p.about}</p>
        </div>
      ))}
    </div>
  );
}

/** An account's part of the change, with a bar against the account that moved most. */
function MoverRow({ a, widest }: { a: WindowAccount; widest: number }) {
  const up = a.change_cents >= 0;
  const owed = a.group === "cards" || a.group === "loans";
  let detail: React.ReactNode = null;
  if (a.market_cents !== null && a.other_cents !== null) {
    const marketShown = Math.abs(a.market_cents) >= 100;
    const word = a.other_cents >= 0 ? "added" : "taken out";
    const added = Math.abs(a.other_cents) >= 100 && (
      <>
        {marketShown ? word : word.charAt(0).toUpperCase() + word.slice(1)} <Money cents={Math.abs(a.other_cents)} />
      </>
    );
    detail = (
      <>
        {marketShown && (
          <>
            Markets <Money cents={a.market_cents} signed />
          </>
        )}
        {marketShown && added && " · "}
        {added}
      </>
    );
  } else if (a.split === "none") {
    detail = "No holdings detail to split markets from new money";
  } else if (owed) {
    detail = up ? "Paid down" : "Owed more";
  }
  return (
    <li className="py-2.5 text-sm">
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 wrap-anywhere">{a.name}</span>
        <Money cents={a.change_cents} signed className={cn("shrink-0 font-medium tabular-nums", up ? "text-positive" : "text-negative")} />
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
        <div
          className={cn("h-full rounded-full", up ? "bg-positive" : "bg-negative")}
          style={{ width: `${Math.max((Math.abs(a.change_cents) / Math.max(widest, 1)) * 100, 2)}%` }}
        />
      </div>
      {(detail || a.new) && (
        <p className="mt-1 text-xs text-muted-foreground">
          {detail}
          {detail && a.new && " · "}
          {a.new && `since ${shortDay.format(new Date(a.since))}, when it was first synced`}
        </p>
      )}
    </li>
  );
}

/** Net worth across the window, with a dashed line at where it started. */
function WindowChart({ points, from, up }: { points: number[]; from: string; up: boolean }) {
  const w = 320;
  const h = 96;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const y = (v: number) => h - 6 - ((v - min) / span) * (h - 12);
  const xy = points.map((p, i) => [(i / (points.length - 1)) * w, y(p)] as const);
  const line = xy.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
  const color = up ? "var(--positive)" : "var(--negative)";
  return (
    <figure>
      <svg
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Net worth from ${formatMoney(points[0]!)} to ${formatMoney(points[points.length - 1]!)}, low ${formatMoney(min)}, high ${formatMoney(max)}.`}
        className="money h-24 w-full"
      >
        <defs>
          <linearGradient id="nw-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.2" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <line x1="0" x2={w} y1={y(points[0]!)} y2={y(points[0]!)} stroke="var(--muted-foreground)" strokeOpacity="0.5" strokeDasharray="3 4" vectorEffect="non-scaling-stroke" />
        <path d={`${line} L${w},${h} L0,${h} Z`} fill="url(#nw-fill)" />
        <path d={line} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption className="mt-1 flex justify-between text-xs text-muted-foreground" aria-hidden>
        <span>{shortDay.format(new Date(from))}</span>
        <span>
          Low <Money cents={min} style="compact" /> · High <Money cents={max} style="compact" />
        </span>
        <span>Today</span>
      </figcaption>
    </figure>
  );
}

function Section({ title, total, children }: { title: string; total?: number; children: React.ReactNode }) {
  return (
    <section>
      <div className="mb-1 flex items-baseline justify-between">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h3>
        {total !== undefined && (
          <span className="text-xs text-muted-foreground">
            <Money cents={total} />
          </span>
        )}
      </div>
      <ul className="divide-y">{children}</ul>
    </section>
  );
}

function list(names: string[]) {
  return names.length < 3 ? names.join(" and ") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}
