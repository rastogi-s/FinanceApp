"use client";

import Link from "next/link";
import { useState } from "react";
import { ChevronRight, Flag, Plus, Target } from "lucide-react";
import { GoalSheet } from "@/components/goal-sheet";
import { Money } from "@/components/money";
import { BackLink, Notice } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { COLORS, type Goal, type GoalsState, ICONS, PRIORITY, monthYear } from "@/lib/goals";
import { useApi } from "@/lib/household";
import { cn } from "@/lib/utils";

type Draft = { goal: Goal | null; suggestion?: GoalsState["suggestions"][number] };

export default function GoalsPage() {
  const { data, error } = useApi<GoalsState>("/api/goals");
  const [local, setLocal] = useState<GoalsState | null>(null);
  const [open, setOpen] = useState<Draft | null>(null);
  const s = local ?? data;

  return (
    <>
      <div className="mb-5">
        <BackLink href="/plan">Plan</BackLink>
        <div className="flex items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Goals</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              What you&apos;re saving for, and the money set aside for each. Cash set aside for a goal no longer counts as your emergency fund.
            </p>
          </div>
          {s && (
            <Button size="sm" className="mb-0.5" onClick={() => setOpen({ goal: null })}>
              <Plus /> New goal
            </Button>
          )}
        </div>
      </div>
      {error && !s && (
        <Notice icon={<Target className="size-5" />} title="Couldn't load your goals">
          {error}. Check that the server is running, then reload the page.
        </Notice>
      )}
      {!s && !error && (
        <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-32 rounded-xl" />
          <Skeleton className="h-40 rounded-xl" />
          <Skeleton className="h-40 rounded-xl" />
        </div>
      )}
      {s && <Goals s={s} onOpen={setOpen} />}
      {s && (
        <GoalSheet
          open={open !== null}
          onOpenChange={(o) => !o && setOpen(null)}
          state={s}
          goal={open?.goal ?? null}
          suggestion={open?.suggestion}
          onSaved={setLocal}
        />
      )}
    </>
  );
}

function Goals({ s, onOpen }: { s: GoalsState; onOpen: (d: Draft) => void }) {
  const saving = s.goals.filter((g) => g.kind !== "emergency");
  const taken = new Set(saving.map((g) => g.name.toLowerCase()));
  const ideas = s.suggestions.filter((x) => !taken.has(x.name.toLowerCase()));
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <Summary s={s} />
      {s.goals.map((g) => (
        <GoalCard key={g.id} g={g} onOpen={() => onOpen({ goal: g })} />
      ))}
      {ideas.length > 0 && (
        <Card className={cn(saving.length === 0 && "border-primary/30 bg-accent/40", "md:col-span-2")}>
          <CardContent className="pt-5">
            <p className="font-medium">{saving.length === 0 ? "Saving for something?" : "Another goal?"}</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Pick one to start, or make your own. You choose the amount, the date and which money goes toward it.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {ideas.map((x) => {
                const Icon = ICONS[x.icon];
                return (
                  <button
                    key={x.name}
                    type="button"
                    onClick={() => onOpen({ goal: null, suggestion: x })}
                    className="inline-flex items-center gap-2 rounded-full border bg-card px-3.5 py-2 text-sm font-medium transition-colors hover:bg-muted"
                  >
                    <Icon className="size-4 text-muted-foreground" aria-hidden />
                    {x.name}
                  </button>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Summary({ s }: { s: GoalsState }) {
  const m = s.summary;
  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>Each month</CardTitle>
        <Flag className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-2xl font-semibold tracking-tight">
              <Money cents={m.needed_cents} />
            </p>
            <p className="text-xs text-muted-foreground">what goals with a date need</p>
          </div>
          <div>
            <p className="text-2xl font-semibold tracking-tight">{m.monthly_savings_cents === null ? "—" : <Money cents={m.monthly_savings_cents} />}</p>
            <p className="text-xs text-muted-foreground">what you save, from your pay and spending</p>
          </div>
        </div>
        <p className="text-sm text-muted-foreground">
          {m.monthly_savings_cents === null ? (
            <>
              The app hasn&apos;t seen any pay yet, so each goal shows what it needs. Add a pay stub on the <Link href="/budget#pay" className="font-medium text-primary">Budget</Link>{" "}
              page and it shares out what you actually save.
            </>
          ) : m.short_cents ? (
            <>
              That&apos;s <Money cents={m.short_cents} className="font-medium text-foreground" /> a month more than you save today. Moving a date out, or a
              lower-priority goal, closes the gap.
            </>
          ) : (
            <>Your savings cover every dated goal. What&apos;s left goes to the goals with no date, the higher priority ones first.</>
          )}{" "}
          <Money cents={m.set_aside_cents} className="font-medium text-foreground" /> is set aside for goals today.
        </p>
      </CardContent>
    </Card>
  );
}

function status(g: Goal): { text: React.ReactNode; badge?: { label: string; variant: "positive" | "warning" | "muted" } } {
  const pace = g.monthly_cents ? <Money cents={g.monthly_cents} /> : null;
  const by = g.target_date ? monthYear.format(new Date(g.target_date)) : null;
  const when = g.projected_month ? monthYear.format(new Date(g.projected_month)) : null;
  switch (g.status) {
    case "done":
      return { text: "Everything you set out to save is there.", badge: { label: "Reached", variant: "positive" } };
    case "no_target":
      return {
        text:
          g.kind === "emergency"
            ? "Once the budget has bills or essentials in it, the target is months of those."
            : "Set an amount to see progress.",
      };
    case "on_track":
      return {
        text: (
          <>
            {pace} a month gets there by {when}
            {by && when !== by ? `, ahead of ${by}` : ""}.
          </>
        ),
        badge: { label: "On track", variant: "positive" },
      };
    case "behind":
      return {
        text: (
          <>
            Needs <Money cents={g.needed_cents ?? 0} /> a month to make {by}.{" "}
            {when ? (
              <>
                At {pace ?? "$0"} a month, {when}.
              </>
            ) : (
              "Nothing is going in yet."
            )}
          </>
        ),
        badge: { label: "Behind", variant: "warning" },
      };
    default:
      return {
        text:
          when && pace ? (
            <>
              At {pace} a month, around {when}.
            </>
          ) : (
            "Nothing going in yet: add a date, or set a monthly amount."
          ),
      };
  }
}

function GoalCard({ g, onOpen }: { g: Goal; onOpen: () => void }) {
  const Icon = ICONS[g.icon] ?? Target;
  const color = COLORS[g.color] ?? COLORS.teal;
  const st = status(g);
  const ratio = g.target_cents > 0 ? Math.min(g.saved_cents / g.target_cents, 1) : 0;
  return (
    <Card className="transition-colors hover:bg-muted/40">
      <button type="button" onClick={onOpen} className="block w-full px-5 py-4 text-left" aria-label={`${g.name}: change`}>
        <div className="flex items-center gap-3">
          <span className={cn("grid size-10 shrink-0 place-items-center rounded-2xl", color.tile)} aria-hidden>
            <Icon className="size-5" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-medium">{g.name}</span>
            <span className="block text-xs text-muted-foreground">
              {PRIORITY[g.priority]} priority{g.target_date ? ` · by ${monthYear.format(new Date(g.target_date))}` : ""}
            </span>
          </span>
          {st.badge && <Badge variant={st.badge.variant}>{st.badge.label}</Badge>}
          <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        </div>
        <p className="mt-4 text-sm">
          <span className="text-xl font-semibold tracking-tight">
            <Money cents={g.saved_cents} />
          </span>{" "}
          {g.target_cents > 0 && (
            <span className="text-muted-foreground">
              of <Money cents={g.target_cents} />
              {g.percent !== null && ` · ${Math.floor(g.percent)}%`}
            </span>
          )}
        </p>
        <div
          role="progressbar"
          aria-label={`${g.name} progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(ratio * 100)}
          className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted"
        >
          <div className={cn("h-full rounded-full transition-[width] duration-500", ratio >= 1 ? "bg-positive" : color.bar)} style={{ width: `${ratio * 100}%` }} />
        </div>
        <p className="mt-2.5 text-sm text-muted-foreground">{st.text}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {g.auto ? (
            "Counts your checking and savings, less what's set aside for other goals."
          ) : g.funding.length === 0 ? (
            "No money set aside yet."
          ) : (
            <>
              From{" "}
              {g.funding.map((f, i) => (
                <span key={f.account_id}>
                  {i > 0 && (i === g.funding.length - 1 ? " and " : ", ")}
                  {f.account}
                  {f.cents !== null && (
                    <>
                      {" "}
                      (<Money cents={f.cents} />
                      {f.short && <span className="text-warning">, only <Money cents={f.counted_cents} /> there</span>})
                    </>
                  )}
                </span>
              ))}
              .
            </>
          )}
        </p>
      </button>
    </Card>
  );
}
