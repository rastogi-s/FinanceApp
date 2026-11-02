"use client";

import { useMemo, useState } from "react";
import { Check } from "lucide-react";
import { Money } from "@/components/money";
import { Sheet } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { resetHousehold, useApi, type Transaction } from "@/lib/household";
import { cn } from "@/lib/utils";

type Category = { id: number; name: string; kind: string };

const KIND_LABEL: Record<string, string> = {
  flexible: "Everyday",
  fixed: "Bills",
  income: "Money in",
  transfer: "Between your accounts",
  other: "Other",
};

/** Two taps: tap the transaction, tap the category. "Always" makes a rule for that merchant. */
export function CategorySheet({ transaction, onClose }: { transaction: Transaction | null; onClose: () => void }) {
  const { data: categories } = useApi<Category[]>(transaction ? "/api/categories" : null);
  const [always, setAlways] = useState(true);
  const [saving, setSaving] = useState<number | null>(null);
  const { state } = useAuth();
  const demo = state.status === "signed-in" && state.demo;
  const groups = useMemo(() => {
    const g = new Map<string, Category[]>();
    for (const c of categories ?? []) g.set(c.kind, [...(g.get(c.kind) ?? []), c]);
    return ["flexible", "fixed", "income", "other", "transfer"].filter((k) => g.has(k)).map((k) => [k, g.get(k)!] as const);
  }, [categories]);

  const pick = async (c: Category) => {
    if (!transaction) return;
    if (demo) {
      toast("Changing categories is off while the app shows sample data.");
      return;
    }
    setSaving(c.id);
    try {
      const r = await api<{ also_changed: number }>(`/api/transactions/${transaction.id}`, {
        method: "PATCH",
        body: { category_id: c.id, remember: always && c.kind !== "transfer" },
      });
      toast(
        r.also_changed > 0
          ? `Moved to ${c.name}, with ${r.also_changed} other${r.also_changed === 1 ? "" : "s"} from ${transaction.merchant}.`
          : `Moved to ${c.name}.`,
      );
      resetHousehold(true);
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSaving(null);
    }
  };

  return (
    <Sheet
      open={transaction !== null}
      onOpenChange={(o) => !o && onClose()}
      title={transaction?.merchant ?? ""}
      description={transaction ? <Money cents={transaction.amount_cents} style="exact" /> : undefined}
    >
      <div className="grid grid-cols-1 gap-5 pb-2">
        {/* Moved to another month by the app: that's likely why it was tapped, so it comes first. */}
        {transaction?.bill && transaction.counts_toward && (
          <CountsToward key={transaction.id} transaction={transaction} demo={demo} onDone={onClose} />
        )}
        {groups.map(([kind, items]) => (
          <div key={kind}>
            <p className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">{KIND_LABEL[kind] ?? kind}</p>
            <div className="flex flex-wrap gap-2">
              {items.map((c) => {
                const current = c.id === transaction?.category_id || c.name === transaction?.category;
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => pick(c)}
                    disabled={saving !== null}
                    aria-pressed={current}
                    className={cn(
                      "inline-flex h-10 items-center gap-1.5 rounded-full border px-4 text-sm font-medium transition",
                      current ? "border-transparent bg-primary text-primary-foreground" : "bg-card hover:bg-muted",
                      saving === c.id && "opacity-60",
                    )}
                  >
                    {current && <Check className="size-4" aria-hidden />}
                    {c.name}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
        {transaction && (
          <div className="rounded-2xl border p-4">
            <Switch
              checked={always}
              onCheckedChange={setAlways}
              label={`Always for ${transaction.merchant}`}
              description="Every past and future transaction from this merchant goes in the same place."
            />
          </div>
        )}
        {transaction?.bill && !transaction.counts_toward && (
          <CountsToward key={transaction.id} transaction={transaction} demo={demo} onDone={onClose} />
        )}
      </div>
    </Sheet>
  );
}

const monthOnly = new Intl.DateTimeFormat("en-US", { month: "long", timeZone: "UTC" });

function addMonths(first: string, n: number): string {
  const [y, m] = first.split("-").map(Number);
  const d = new Date(Date.UTC(y!, m! - 1 + n, 1));
  return d.toISOString().slice(0, 10);
}

/** Which month's budget a payment counts in: the month it was paid in, or the one before or after. Rent paid on
 * the 28th is next month's, and the app works most of these out by itself; this is for the ones it gets wrong. */
function CountsToward({ transaction, demo, onDone }: { transaction: Transaction; demo: boolean; onDone: () => void }) {
  const [saving, setSaving] = useState<string | null>(null);
  const paid = `${transaction.date.slice(0, 7)}-01`;
  const counts = transaction.counts_toward ?? paid;
  const options = [addMonths(paid, -1), paid, addMonths(paid, 1)];
  const name = (m: string) => monthOnly.format(new Date(`${m}T12:00:00Z`));

  const save = async (month: string | null) => {
    if (demo) {
      toast("Changing the month is off while the app shows sample data.");
      return;
    }
    setSaving(month ?? "auto");
    try {
      const r = await api<{ counts_toward: string | null }>(`/api/transactions/${transaction.id}/month`, {
        method: "PATCH",
        body: { month },
      });
      toast(`Counts in ${name(r.counts_toward ?? paid)}'s budget.`);
      resetHousehold(true);
      onDone();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSaving(null);
    }
  };

  return (
    <div>
      <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">Counts in the budget for</p>
      <p className="mb-3 text-xs text-muted-foreground">
        {transaction.counts_by_hand
          ? "You picked this month."
          : counts === paid
            ? `Paid in ${name(paid)}. Pick another month if this was for the month before or after.`
            : counts > paid
              ? `Paid ahead in ${name(paid)}, so it counts in ${name(counts)}, the month it's for.`
              : `Paid in ${name(paid)}, but it's ${name(counts)}'s bill, going by when you usually pay it.`}
      </p>
      <div className="flex flex-wrap gap-2">
        {options.map((m) => {
          const on = m === counts;
          return (
            <button
              key={m}
              type="button"
              onClick={() => !on && save(m)}
              disabled={saving !== null}
              aria-pressed={on}
              className={cn(
                "inline-flex h-10 items-center gap-1.5 rounded-full border px-4 text-sm font-medium transition",
                on ? "border-transparent bg-primary text-primary-foreground" : "bg-card hover:bg-muted",
                saving === m && "opacity-60",
              )}
            >
              {on && <Check className="size-4" aria-hidden />}
              {name(m)}
            </button>
          );
        })}
        {transaction.counts_by_hand && (
          <button
            type="button"
            onClick={() => save(null)}
            disabled={saving !== null}
            className="inline-flex h-10 items-center rounded-full px-3 text-sm font-medium text-primary hover:underline"
          >
            Let the app decide
          </button>
        )}
      </div>
    </div>
  );
}
