"use client";

import Link from "next/link";
import { ArrowDownRight, ArrowUpRight, Bell, ChevronRight, TrendingUp } from "lucide-react";
import { Sparkline } from "@/components/charts";
import { Money, formatMoney } from "@/components/money";
import { HouseholdPage } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";

const time = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });

export default function Home() {
  return (
    <HouseholdPage title="Home">
      {(h) => {
        const change = h.net_worth.change_today_cents;
        const up = change >= 0;
        const history = h.net_worth.history.map((p) => p.cents);
        const perDay = Math.floor(h.safe_to_spend.cents / Math.max(h.safe_to_spend.days_left, 1));
        return (
          <div className="grid gap-4">
            <Card className="overflow-hidden">
              <CardHeader>
                <CardTitle>Net worth</CardTitle>
                <Badge variant={up ? "positive" : "negative"}>
                  {up ? <ArrowUpRight className="size-3.5" aria-hidden /> : <ArrowDownRight className="size-3.5" aria-hidden />}
                  <Money cents={change} signed /> today
                </Badge>
              </CardHeader>
              <CardContent className="pb-0">
                <p className="text-4xl font-semibold tracking-tight md:text-5xl">
                  <Money cents={h.net_worth.cents} />
                </p>
              </CardContent>
              <Sparkline
                points={history}
                label={`Net worth over the last 90 days, from ${formatMoney(history[0] ?? 0)} to ${formatMoney(h.net_worth.cents)}.`}
              />
            </Card>

            <div className="grid grid-cols-2 gap-4">
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
            </div>

            <Card>
              <CardHeader>
                <CardTitle>Closest to their limit</CardTitle>
                <Link href="/budget" className="flex items-center text-sm font-medium text-primary">
                  Budget <ChevronRight className="size-4" aria-hidden />
                </Link>
              </CardHeader>
              <CardContent className="grid gap-4">
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

            <Card>
              <CardHeader>
                <CardTitle>Alerts</CardTitle>
                <Bell className="size-4 text-muted-foreground" aria-hidden />
              </CardHeader>
              <CardContent className="pt-1">
                <ul className="divide-y">
                  {h.alerts.map((a) => (
                    <li key={a.id} className="flex items-start gap-3 py-3">
                      <span className={a.kind === "budget" ? "mt-1.5 size-2 rounded-full bg-warning" : "mt-1.5 size-2 rounded-full bg-chart-2"} aria-hidden />
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
          </div>
        );
      }}
    </HouseholdPage>
  );
}
