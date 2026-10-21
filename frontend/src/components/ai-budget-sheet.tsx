"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertTriangle, ArrowRight, Check, EyeOff, Sparkles } from "lucide-react";
import { Money } from "@/components/money";
import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { usd } from "@/lib/ai";
import { api } from "@/lib/api";
import type { BudgetLine } from "@/lib/budget";
import { cn } from "@/lib/utils";

type Offer = {
  available: boolean;
  problem: string | null;
  provider: string | null;
  model: string | null;
  max_cost: number | null;
  months: number;
  categories: number;
  stubs: number;
  preview: string | null;
};

type Suggested = {
  category_id: number;
  category: string;
  kind: "fixed" | "flexible";
  current_cents: number;
  typical_cents: number;
  suggested_cents: number;
  reason: string;
};
type Suggestion = { summary: string; lines: Suggested[]; model: string; cost: number };

/** Draft with AI: what goes, then a suggested limit per line with its reason; the person keeps the lines they want. */
export function AIBudgetSheet({
  open,
  onOpenChange,
  lines,
  save,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  lines: BudgetLine[];
  save: (lines: { category_id: number; limit_cents: number }[]) => Promise<unknown>;
}) {
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="Draft with AI"
      description="A budget suggested from your spending, your edits, your pay stubs and your savings goal."
    >
      {open && <Flow lines={lines} save={save} done={() => onOpenChange(false)} />}
    </Sheet>
  );
}

function Flow({
  lines,
  save,
  done,
}: {
  lines: BudgetLine[];
  save: (lines: { category_id: number; limit_cents: number }[]) => Promise<unknown>;
  done: () => void;
}) {
  const [offer, setOffer] = useState<Offer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());

  useEffect(() => {
    api<Offer>("/api/budget/ai").then(setOffer, (e: Error) => setError(e.message));
  }, []);

  const ask = async () => {
    setBusy(true);
    try {
      const s = await api<Suggestion>("/api/budget/ai", { method: "POST" });
      setSuggestion(s);
      setPicked(new Set(s.lines.filter((l) => l.suggested_cents !== l.current_cents).map((l) => l.category_id)));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const keep = async () => {
    if (!suggestion) return;
    const merged = new Map(lines.map((l) => [l.category_id, l.limit_cents]));
    for (const l of suggestion.lines) if (picked.has(l.category_id)) merged.set(l.category_id, l.suggested_cents);
    setBusy(true);
    try {
      await save([...merged].map(([category_id, limit_cents]) => ({ category_id, limit_cents })));
      toast(`Saved ${picked.size} change${picked.size === 1 ? "" : "s"}. Your old budget is in History.`);
      done();
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(false);
    }
  };

  if (error) return <p className="text-sm text-negative">{error}</p>;
  if (!offer) return <Skeleton className="h-48 rounded-2xl" />;

  if (suggestion) {
    const changes = suggestion.lines.filter((l) => l.suggested_cents !== l.current_cents);
    const before = suggestion.lines.reduce((s, l) => s + l.current_cents, 0);
    const after = suggestion.lines.reduce((s, l) => s + (picked.has(l.category_id) ? l.suggested_cents : l.current_cents), 0);
    return (
      <div className="grid grid-cols-1 gap-4 pb-2">
        <div className="flex items-start gap-3 rounded-2xl bg-accent/60 p-4">
          <Sparkles className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden />
          <p className="text-sm leading-relaxed">{suggestion.summary}</p>
        </div>
        <ul className="grid grid-cols-1 gap-2" aria-label="Suggested limits">
          {suggestion.lines.map((l) => {
            const same = l.suggested_cents === l.current_cents;
            const on = picked.has(l.category_id);
            return (
              <li key={l.category_id}>
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={same ? undefined : on}
                  disabled={same}
                  onClick={() =>
                    setPicked((p) => {
                      const n = new Set(p);
                      if (n.has(l.category_id)) n.delete(l.category_id);
                      else n.add(l.category_id);
                      return n;
                    })
                  }
                  className={cn(
                    "flex w-full items-start gap-3 rounded-2xl border p-3 text-left transition-colors",
                    on && !same ? "border-primary/50 bg-accent/40" : "border-border",
                  )}
                >
                  <span
                    className={cn(
                      "mt-0.5 grid size-5 shrink-0 place-items-center rounded-md border",
                      same ? "border-transparent text-muted-foreground" : on ? "border-primary bg-primary text-primary-foreground" : "border-muted-foreground/40",
                    )}
                    aria-hidden
                  >
                    {(on || same) && <Check className="size-3.5" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline justify-between gap-2">
                      <span className="text-sm font-medium">{l.category}</span>
                      <span className="flex shrink-0 items-center gap-1.5 text-sm tabular-nums">
                        {same ? (
                          <span className="text-muted-foreground">
                            <Money cents={l.current_cents} /> stays
                          </span>
                        ) : (
                          <>
                            <span className="text-muted-foreground line-through decoration-muted-foreground/50">
                              {l.current_cents ? <Money cents={l.current_cents} /> : "none"}
                            </span>
                            <ArrowRight className="size-3.5 text-muted-foreground" aria-hidden />
                            <span className="font-semibold">
                              <Money cents={l.suggested_cents} />
                            </span>
                          </>
                        )}
                      </span>
                    </span>
                    <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">{l.reason}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        <p className="text-xs text-muted-foreground">
          These lines: <Money cents={before} /> a month now, <Money cents={after} /> with what you picked. Suggested by {suggestion.model} for{" "}
          {usd(suggestion.cost)}. Lines it didn&apos;t mention stay as they are.
        </p>
        <div className="grid grid-cols-2 gap-2">
          <Button variant="outline" size="lg" onClick={done} disabled={busy}>
            Not now
          </Button>
          <Button size="lg" onClick={keep} disabled={busy || picked.size === 0}>
            {changes.length === 0 ? "Nothing to change" : `Save ${picked.size} change${picked.size === 1 ? "" : "s"}`}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 pb-2">
      <div className="grid grid-cols-1 gap-2 rounded-2xl border p-4 text-sm">
        <p className="font-medium">What would be sent</p>
        <p className="flex items-start gap-2 text-muted-foreground">
          <EyeOff className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            Totals only: {offer.months} month{offer.months === 1 ? "" : "s"} of spending in {offer.categories} categor
            {offer.categories === 1 ? "y" : "ies"}, your budget and which lines you changed, {offer.stubs} pay stub
            {offer.stubs === 1 ? "" : "s"}, your savings rate and goal. No merchants, names or account numbers.
          </span>
        </p>
        {offer.preview && (
          <details className="rounded-xl bg-muted/60 px-3 py-2">
            <summary className="cursor-pointer text-xs font-medium">See exactly what goes</summary>
            <pre className="mt-2 max-h-56 overflow-auto font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words text-muted-foreground">
              {offer.preview}
            </pre>
          </details>
        )}
        {offer.available && (
          <p className="text-xs text-muted-foreground">
            To {offer.provider} ({offer.model}). {offer.max_cost ? `At most ${usd(offer.max_cost)}, usually much less. ` : "Free. "}
            Nothing changes until you pick the lines to keep.
          </p>
        )}
      </div>
      {offer.available ? (
        <Button size="lg" onClick={ask} disabled={busy}>
          <Sparkles /> {busy ? "Thinking it through…" : "Suggest a budget"}
        </Button>
      ) : (
        <>
          <p className="flex items-start gap-2 text-sm font-medium text-warning">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /> {offer.problem}
          </p>
          {offer.problem && !offer.problem.startsWith("There isn't") && (
            <Button size="lg" asChild>
              <Link href="/settings/ai">Settings, AI</Link>
            </Button>
          )}
        </>
      )}
    </div>
  );
}
