"use client";

import Link from "next/link";
import { useState } from "react";
import { type Guide, GuideCard } from "@/components/guide";
import { Bell, ChevronRight, Sparkles, TrendingUp } from "lucide-react";
import { FreedomTile, GoalTile, Greeting, NeedsYou, NetWorthHero } from "@/components/home";
import { Money } from "@/components/money";
import { Column, Columns, HouseholdPage } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { monthName } from "@/lib/budget";
import { KIND_DOT, type LinkedHousehold, alertHref, resetHousehold, useApi } from "@/lib/household";
import { cn } from "@/lib/utils";

const time = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
});
const shortDay = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
});

export default function Home() {
  const { data: guide, reload } = useApi<Guide>("/api/guide");
  const hideGuide = async () => {
    try {
      await api("/api/guide", { method: "PUT", body: { hidden: true } });
      reload();
      toast("Hidden. The guide stays in Settings.");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <HouseholdPage title="Home" heading={<Greeting />} wide before={guide && <GuideCard guide={guide} onHide={hideGuide} />}>
      {(h) => (
        // Phone: what needs you, net worth, the tiles, then the rest. Laptop: what needs you across the top, the
        // chart beside the tiles, then two columns.
        <div data-wide className="grid grid-cols-1 gap-4 xl:grid-cols-12 xl:items-start">
          <NeedsYou h={h} className="xl:col-span-12" />
          <NetWorthHero h={h} className="xl:col-span-8" />
          <div className="grid grid-cols-2 gap-4 xl:col-span-4 [&>*:last-child:nth-child(odd)]:col-span-2">
            <FirstTile h={h} />
            <SecondTile h={h} />
            <FreedomTile h={h} />
            <GoalTile />
          </div>
          <Columns className="xl:col-span-12">
            <Column>
              {h.budgets_closest.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle>Closest to their limit</CardTitle>
                    <Link href="/budget" className="flex items-center text-sm font-medium text-primary">
                      Budget <ChevronRight className="size-4" aria-hidden />
                    </Link>
                  </CardHeader>
                  <CardContent className="grid grid-cols-1 gap-4">
                    {h.budgets_closest.map((b) => (
                      <div key={b.category}>
                        <div className="mb-1.5 flex items-baseline justify-between text-sm">
                          <span className="font-medium">{b.category}</span>
                          <span className="text-muted-foreground">
                            <Money cents={b.spent_cents} /> of <Money cents={b.limit_cents} />
                          </span>
                        </div>
                        <Progress value={b.spent_cents} max={b.limit_cents} label={`${b.category} budget used`} />
                      </div>
                    ))}
                  </CardContent>
                </Card>
              )}
              {h.alerts.length > 0 && <Alerts h={h} />}
            </Column>
            <Column>{!h.demo && <Accounts h={h} />}</Column>
          </Columns>
        </div>
      )}
    </HouseholdPage>
  );
}

/** "Safe to spend" once there's an everyday budget; until then, what's been spent this month. */
function FirstTile({ h }: { h: LinkedHousehold }) {
  if (h.safe_to_spend) {
    const perDay = Math.floor(h.safe_to_spend.cents / Math.max(h.safe_to_spend.days_left, 1));
    return (
      <Card>
        <CardHeader>
          <CardTitle>Safe to spend</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-2xl font-semibold tracking-tight">
            <Money cents={h.safe_to_spend.cents} />
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            <Money cents={perDay} /> a day for {h.safe_to_spend.days_left} days
          </p>
        </CardContent>
      </Card>
    );
  }
  const s = h.spending;
  const diff = s ? s.month_cents - s.last_month_same_day_cents : 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Spent this month</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold tracking-tight">
          <Money cents={s?.month_cents ?? 0} />
        </p>
        {s && s.last_month_same_day_cents > 0 && (
          <p className="mt-1 text-xs text-muted-foreground">
            <Money cents={Math.abs(diff)} /> {diff > 0 ? "more" : "less"} than by now last month
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function SecondTile({ h }: { h: LinkedHousehold }) {
  if (h.savings_rate) {
    return (
      <Link href="/budget?savings=1" className="block">
        <Card className="h-full transition-colors hover:bg-muted/40">
          <CardHeader>
            <CardTitle>Savings rate</CardTitle>
            <TrendingUp className="size-4 text-positive" aria-hidden />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-semibold tracking-tight">{h.savings_rate.percent.toFixed(0)}%</p>
            <p className="mt-1 text-xs text-muted-foreground">
              of {h.savings_rate.basis === "gross" ? "gross" : "take-home"} pay in {monthName.format(new Date(h.savings_rate.month))}
            </p>
          </CardContent>
        </Card>
      </Link>
    );
  }
  const review = h.spending?.needs_review ?? 0;
  return (
    <Link href={review ? "/review" : "/spending"} className="block">
      <Card className="h-full transition-colors hover:bg-muted/40">
        <CardHeader>
          <CardTitle>{review ? "Categories to check" : "All sorted"}</CardTitle>
          <Sparkles className="size-4 text-primary" aria-hidden />
        </CardHeader>
        <CardContent>
          <p className="text-2xl font-semibold tracking-tight">{review}</p>
          <p className="mt-1 text-xs text-muted-foreground">{review ? "One answer per merchant" : "Every transaction has a home"}</p>
        </CardContent>
      </Card>
    </Link>
  );
}

function Accounts({ h }: { h: LinkedHousehold }) {
  const groups = [
    { title: "Cash", rows: h.accounts.cash, sign: 1 },
    { title: "Investments", rows: h.accounts.investments ?? [], sign: 1 },
    { title: "Cards and loans", rows: h.accounts.debts, sign: -1 },
  ].filter((g) => g.rows.length);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Accounts</CardTitle>
        <Link href="/settings/accounts" className="flex items-center text-sm font-medium text-primary">
          Manage <ChevronRight className="size-4" aria-hidden />
        </Link>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4">
        {groups.map((g) => (
          <div key={g.title}>
            <div className="mb-1 flex items-baseline justify-between text-xs font-medium tracking-wide text-muted-foreground uppercase">
              <span>{g.title}</span>
              <Money cents={g.sign * g.rows.reduce((s, r) => s + r.cents, 0)} />
            </div>
            <ul className="divide-y">
              {g.rows.map((r) => (
                <li key={r.id ?? r.name} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                  <span className="min-w-0 truncate">{r.name}</span>
                  {r.as_of ? (
                    <Money cents={g.sign * r.cents} className="font-medium" />
                  ) : (
                    <span className="text-xs text-muted-foreground">No balance yet</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function Alerts({ h }: { h: LinkedHousehold }) {
  const [cleared, setCleared] = useState(false);
  if (cleared) return null;
  const clear = async () => {
    if (h.demo) return toast("Alerts stay put while the app shows sample data.");
    try {
      await api("/api/alerts/read", { method: "POST" });
      setCleared(true);
      resetHousehold();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>Alerts</CardTitle>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={clear}>
            Clear
          </Button>
          <Link href="/settings/alerts" className="flex items-center text-sm font-medium text-primary" aria-label="All alerts">
            <Bell className="size-4" aria-hidden />
            <ChevronRight className="size-4" aria-hidden />
          </Link>
        </div>
      </CardHeader>
      <CardContent className="pt-1">
        <ul className="divide-y">
          {h.alerts.map((a) => {
            const href = alertHref(a);
            const row = (
              <>
                <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", KIND_DOT[a.kind])} aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{a.title}</p>
                  <p className="text-sm text-muted-foreground">{a.body}</p>
                </div>
                <time dateTime={a.at} className="text-xs text-muted-foreground">
                  {when(a.at)}
                </time>
                {href && <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />}
              </>
            );
            return (
              <li key={a.id}>
                {href ? (
                  <Link href={href} className="-mx-2 flex items-start gap-3 rounded-lg px-2 py-3 transition-colors hover:bg-muted/60">
                    {row}
                  </Link>
                ) : (
                  <div className="flex items-start gap-3 py-3">{row}</div>
                )}
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

function when(iso: string) {
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString() ? time.format(d) : shortDay.format(d);
}
