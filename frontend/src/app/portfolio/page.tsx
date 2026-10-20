"use client";

import { Money } from "@/components/money";
import { HouseholdPage } from "@/components/page";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const COLORS = ["bg-chart-1", "bg-chart-2", "bg-chart-3", "bg-chart-4", "bg-chart-5"];

export default function Portfolio() {
  return (
    <HouseholdPage title="Portfolio" subtitle="Every account combined.">
      {(h) => {
        const total = h.allocation.reduce((s, a) => s + a.cents, 0);
        const holdings = [...h.holdings].sort((a, b) => b.value_cents - a.value_cents);
        return (
          <div className="grid grid-cols-1 gap-4">
            <Card>
              <CardHeader>
                <CardTitle>Allocation</CardTitle>
                <span className="text-sm font-medium">
                  <Money cents={total} />
                </span>
              </CardHeader>
              <CardContent>
                <div className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full" role="img" aria-label={h.allocation.map((a) => `${a.asset_class} ${Math.round((a.cents / total) * 100)}%`).join(", ")}>
                  {h.allocation.map((a, i) => (
                    <div key={a.asset_class} className={COLORS[i % COLORS.length]} style={{ width: `${(a.cents / total) * 100}%` }} />
                  ))}
                </div>
                <ul className="mt-4 grid gap-2.5 sm:grid-cols-2">
                  {h.allocation.map((a, i) => (
                    <li key={a.asset_class} className="flex items-center gap-2.5 text-sm">
                      <span className={cn("size-2.5 rounded-full", COLORS[i % COLORS.length])} aria-hidden />
                      <span className="flex-1">{a.asset_class}</span>
                      <span className="text-muted-foreground tabular-nums">{((a.cents / total) * 100).toFixed(1)}%</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Holdings</CardTitle>
              </CardHeader>
              <ul className="divide-y pb-2">
                {holdings.map((x) => (
                  <li key={x.symbol + x.account} className="flex items-center gap-3 px-5 py-3.5">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold">{x.symbol}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {x.name} · {x.account}
                      </p>
                    </div>
                    <div className="text-right">
                      <Money cents={x.value_cents} className="text-sm font-medium" />
                      <p className={cn("text-xs tabular-nums", x.change_today_percent > 0 ? "text-positive" : x.change_today_percent < 0 ? "text-negative" : "text-muted-foreground")}>
                        {x.change_today_percent > 0 ? "+" : ""}
                        {x.change_today_percent.toFixed(2)}%
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        );
      }}
    </HouseholdPage>
  );
}
