"use client";

import { Money } from "@/components/money";
import { HouseholdPage } from "@/components/page";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import type { Budget } from "@/lib/household";

function Group({ title, budgets }: { title: string; budgets: Budget[] }) {
  const spent = budgets.reduce((s, b) => s + b.spent_cents, 0);
  const limit = budgets.reduce((s, b) => s + b.limit_cents, 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <span className="text-sm text-muted-foreground">
          <Money cents={spent} /> of <Money cents={limit} />
        </span>
      </CardHeader>
      <CardContent className="grid gap-5">
        {budgets.map((b) => {
          const left = b.limit_cents - b.spent_cents;
          return (
            <div key={b.category}>
              <div className="mb-1.5 flex items-baseline justify-between text-sm">
                <span className="font-medium">{b.category}</span>
                <span className={left < 0 ? "text-negative" : "text-muted-foreground"}>
                  {left > 0 ? (
                    <>
                      <Money cents={left} /> left
                    </>
                  ) : left === 0 ? (
                    "Paid"
                  ) : (
                    <>
                      <Money cents={-left} /> over
                    </>
                  )}
                </span>
              </div>
              <Progress value={b.spent_cents} max={b.limit_cents} label={`${b.category} budget used`} />
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

export default function BudgetPage() {
  return (
    <HouseholdPage title="Budget" subtitle="This month, drafted from your last six months of spending.">
      {(h) => (
        <div className="grid gap-4">
          <Group title="Flexible" budgets={h.budgets.filter((b) => b.kind === "flexible")} />
          <Group title="Fixed" budgets={h.budgets.filter((b) => b.kind === "fixed")} />
        </div>
      )}
    </HouseholdPage>
  );
}
