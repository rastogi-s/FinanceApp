"use client";

import Link from "next/link";
import { AlertTriangle, ArrowDownRight, ArrowUpRight, Bell, ChevronRight, Sparkles, TrendingUp } from "lucide-react";
import { Sparkline } from "@/components/charts";
import { Money, formatMoney } from "@/components/money";
import { HouseholdPage } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import type { LinkedHousehold } from "@/lib/household";

const time = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });

export default function Home() {
  return (
    <HouseholdPage title="Home">
      {(h) => {
        const change = h.net_worth.change_today_cents;
        const up = change >= 0;
        const history = h.net_worth.history.map((p) => p.cents);
        return (
          <div className="grid grid-cols-1 gap-4">
            {h.health?.stale.map((s) => (
              <Link
                key={s.id}
                href="/settings/accounts"
                className="flex items-center gap-3 rounded-xl border border-warning/30 bg-warning/10 px-4 py-3 text-sm"
              >
                <AlertTriangle className="size-4 shrink-0 text-warning" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{s.institution} stopped syncing.</span>{" "}
                  <span className="text-muted-foreground">Reconnect it or upload a file.</span>
                </span>
                <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
              </Link>
            ))}

            <Card className="overflow-hidden">
              <CardHeader>
                <CardTitle>Net worth</CardTitle>
                {history.length > 1 && (
                  <Badge variant={up ? "positive" : "negative"}>
                    {up ? <ArrowUpRight className="size-3.5" aria-hidden /> : <ArrowDownRight className="size-3.5" aria-hidden />}
                    <Money cents={change} signed /> today
                  </Badge>
                )}
              </CardHeader>
              <CardContent className={history.length > 1 ? "pb-0" : undefined}>
                <p className="text-4xl font-semibold tracking-tight md:text-5xl">
                  <Money cents={h.net_worth.cents} />
                </p>
                {!h.demo && <p className="mt-1 text-xs text-muted-foreground">Synced {time.format(new Date(h.as_of))}</p>}
              </CardContent>
              <Sparkline
                points={history}
                label={`Net worth over the last ${history.length} days, from ${formatMoney(history[0] ?? 0)} to ${formatMoney(h.net_worth.cents)}.`}
              />
            </Card>

            <div className="grid grid-cols-2 gap-4">
              <FirstTile h={h} />
              <SecondTile h={h} />
            </div>

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

            {h.alerts.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>Alerts</CardTitle>
                  <Bell className="size-4 text-muted-foreground" aria-hidden />
                </CardHeader>
                <CardContent className="pt-1">
                  <ul className="divide-y">
                    {h.alerts.map((a) => (
                      <li key={a.id} className="flex items-start gap-3 py-3">
                        <span
                          className={
                            a.kind === "budget" ? "mt-1.5 size-2 rounded-full bg-warning" : "mt-1.5 size-2 rounded-full bg-chart-2"
                          }
                          aria-hidden
                        />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium">{a.title}</p>
                          <p className="text-sm text-muted-foreground">{a.body}</p>
                        </div>
                        <time dateTime={a.at} className="text-xs text-muted-foreground">
                          {time.format(new Date(a.at))}
                        </time>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            )}

            {!h.demo && <Accounts h={h} />}
          </div>
        );
      }}
    </HouseholdPage>
  );
}

/** "Safe to spend" once budgets exist (next phase); until then, what's been spent this month. */
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
      <Card>
        <CardHeader>
          <CardTitle>Savings rate</CardTitle>
          <TrendingUp className="size-4 text-positive" aria-hidden />
        </CardHeader>
        <CardContent>
          <p className="text-2xl font-semibold tracking-tight">{h.savings_rate.percent.toFixed(0)}%</p>
          <p className="mt-1 text-xs text-muted-foreground">of gross pay this month</p>
        </CardContent>
      </Card>
    );
  }
  const review = h.spending?.needs_review ?? 0;
  return (
    <Link href={review ? "/spending?review=1" : "/spending"} className="block">
      <Card className="h-full transition-colors hover:bg-muted/40">
        <CardHeader>
          <CardTitle>{review ? "Needs a category" : "All sorted"}</CardTitle>
          <Sparkles className="size-4 text-primary" aria-hidden />
        </CardHeader>
        <CardContent>
          <p className="text-2xl font-semibold tracking-tight">{review}</p>
          <p className="mt-1 text-xs text-muted-foreground">{review ? "Two taps each" : "Every transaction has a home"}</p>
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
