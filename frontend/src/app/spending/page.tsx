"use client";

import { ArrowLeftRight } from "lucide-react";
import { Money } from "@/components/money";
import { HouseholdPage } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Transaction } from "@/lib/household";

const day = new Intl.DateTimeFormat("en-US", { weekday: "long", month: "short", day: "numeric", timeZone: "UTC" });

function byDay(transactions: Transaction[]) {
  const groups = new Map<string, Transaction[]>();
  for (const t of transactions) groups.set(t.date, [...(groups.get(t.date) ?? []), t]);
  return [...groups.entries()];
}

export default function Spending() {
  return (
    <HouseholdPage title="Spending" subtitle="Transfers between your own accounts are left out of every total.">
      {(h) => {
        const spent = h.transactions.filter((t) => !t.transfer).reduce((s, t) => s - t.amount_cents, 0);
        return (
          <div className="grid gap-4">
            <Card>
              <CardHeader>
                <CardTitle>Last two weeks</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-semibold tracking-tight">
                  <Money cents={spent} />
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  across {h.transactions.filter((t) => !t.transfer).length} purchases
                </p>
              </CardContent>
            </Card>
            {byDay(h.transactions).map(([date, items]) => (
              <section key={date} aria-label={day.format(new Date(date))}>
                <h2 className="mb-2 px-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                  {day.format(new Date(date))}
                </h2>
                <Card>
                  <ul className="divide-y">
                    {items.map((t) => (
                      <li key={t.id} className="flex items-center gap-3 px-5 py-3.5">
                        <div className="grid size-9 shrink-0 place-items-center rounded-full bg-muted text-sm font-semibold text-muted-foreground" aria-hidden>
                          {t.transfer ? <ArrowLeftRight className="size-4" /> : t.merchant[0]}
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium">{t.merchant}</p>
                          <p className="truncate text-xs text-muted-foreground">
                            {t.account}
                          </p>
                        </div>
                        <div className="flex flex-col items-end gap-1">
                          <Money cents={t.amount_cents} style="exact" className={t.transfer ? "text-sm text-muted-foreground" : "text-sm font-medium"} />
                          <Badge variant={t.transfer ? "muted" : "default"}>{t.category}</Badge>
                        </div>
                      </li>
                    ))}
                  </ul>
                </Card>
              </section>
            ))}
          </div>
        );
      }}
    </HouseholdPage>
  );
}
