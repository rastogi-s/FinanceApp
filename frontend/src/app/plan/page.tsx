"use client";

import { ProjectionChart } from "@/components/charts";
import { Money } from "@/components/money";
import { HouseholdPage } from "@/components/page";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function Plan() {
  return (
    <HouseholdPage title="Plan" subtitle="Where today's savings take you. A range, because no one knows future returns.">
      {(h) => {
        const bands = h.projection.bands;
        const in10 = bands[Math.min(10, bands.length - 1)]!;
        const last = bands[bands.length - 1]!;
        return (
          <div className="grid gap-4">
            <Card>
              <CardHeader>
                <CardTitle>Net worth by {last.year}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-semibold tracking-tight">
                  <Money cents={last.low} style="compact" /> – <Money cents={last.high} style="compact" />
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Saving <Money cents={h.projection.monthly_savings_cents} /> a month, at 4% to 8% a year after inflation.
                </p>
                <div className="mt-4">
                  <ProjectionChart bands={bands} />
                </div>
              </CardContent>
            </Card>
            <div className="grid grid-cols-2 gap-4">
              <Card>
                <CardHeader>
                  <CardTitle>In 10 years</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font-semibold tracking-tight">
                    <Money cents={in10.mid} style="compact" />
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">middle of the range</p>
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Monthly savings</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font-semibold tracking-tight">
                    <Money cents={h.projection.monthly_savings_cents} />
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">average of the last 6 months</p>
                </CardContent>
              </Card>
            </div>
          </div>
        );
      }}
    </HouseholdPage>
  );
}
