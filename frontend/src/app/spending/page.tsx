"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowLeftRight, Sparkles } from "lucide-react";
import { CategorySheet } from "@/components/category-sheet";
import { Money } from "@/components/money";
import { HouseholdPage } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Transaction } from "@/lib/household";
import { cn } from "@/lib/utils";

const day = new Intl.DateTimeFormat("en-US", { weekday: "long", month: "short", day: "numeric", timeZone: "UTC" });

function byDay(transactions: Transaction[]) {
  const groups = new Map<string, Transaction[]>();
  for (const t of transactions) groups.set(t.date, [...(groups.get(t.date) ?? []), t]);
  return [...groups.entries()];
}

export default function SpendingPage() {
  return (
    <Suspense>
      <Spending />
    </Suspense>
  );
}

function Spending() {
  const [picking, setPicking] = useState<Transaction | null>(null);
  const [filter, setFilter] = useState<"all" | "review">(useSearchParams().get("review") ? "review" : "all");
  return (
    <>
      <HouseholdPage
        title="Spending"
        subtitle="Transfers between your own accounts are left out of every total. Tap one to change its category."
      >
        {(h) => {
          const real = h.transactions.filter((t) => !t.transfer);
          const spent = real.reduce((s, t) => s - t.amount_cents, 0);
          const first = h.transactions[h.transactions.length - 1]?.date;
          const days = first ? Math.round((new Date(h.as_of).getTime() - new Date(first).getTime()) / 86_400_000) : 0;
          const review = h.transactions.filter((t) => t.needs_review);
          const shown = filter === "review" ? review : h.transactions;
          return (
            <div className="grid grid-cols-1 gap-4">
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

              {review.length > 0 && (
                <div className="flex gap-2" role="group" aria-label="Show">
                  <Chip on={filter === "all"} onClick={() => setFilter("all")}>
                    Everything
                  </Chip>
                  <Chip on={filter === "review"} onClick={() => setFilter("review")}>
                    <Sparkles className="size-3.5" aria-hidden /> Needs a category · {review.length}
                  </Chip>
                </div>
              )}

              {shown.length === 0 && (
                <p className="px-1 py-6 text-center text-sm text-muted-foreground">Nothing left to sort. Nicely done.</p>
              )}

              {byDay(shown).map(([date, items]) => (
                <section key={date} aria-label={day.format(new Date(date))}>
                  <h2 className="mb-2 px-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                    {day.format(new Date(date))}
                  </h2>
                  <Card>
                    <ul className="divide-y">
                      {items.map((t) => (
                        <li key={t.id}>
                          <button
                            type="button"
                            onClick={() => setPicking(t)}
                            className="flex w-full items-center gap-3 px-5 py-3.5 text-left transition-colors hover:bg-muted/50"
                            aria-label={`${t.merchant}, ${t.category}. Change category`}
                          >
                            <div
                              className="grid size-9 shrink-0 place-items-center rounded-full bg-muted text-sm font-semibold text-muted-foreground"
                              aria-hidden
                            >
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
                                className={cn(
                                  "text-sm",
                                  t.transfer ? "text-muted-foreground" : "font-medium",
                                  t.amount_cents > 0 && !t.transfer && "text-positive",
                                )}
                              />
                              <Badge variant={t.transfer ? "muted" : t.needs_review ? "warning" : "default"}>{t.category}</Badge>
                            </div>
                          </button>
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
      <CategorySheet transaction={picking} onClose={() => setPicking(null)} />
    </>
  );
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={cn(
        "inline-flex h-9 items-center gap-1.5 rounded-full border px-3.5 text-sm font-medium transition-colors",
        on ? "border-transparent bg-foreground text-background" : "bg-card text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}
