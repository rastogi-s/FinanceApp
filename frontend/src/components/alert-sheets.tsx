"use client";

import { useState } from "react";
import { Bell, ChartPie, PiggyBank, Trash2, TrendingUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { toast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

export type Rule = {
  id: number;
  kind: "price" | "budget" | "concentration";
  params: Record<string, number | string | null>;
  enabled: boolean;
  summary: string;
  triggered: boolean;
};

export type RuleIn =
  | { kind: "price"; symbol: string; above?: number; below?: number; move_percent?: number }
  | { kind: "budget"; category_id: number | null; percent?: number; forecast?: boolean }
  | { kind: "concentration"; percent: number };

export const KIND_ICON = { price: TrendingUp, budget: PiggyBank, concentration: ChartPie } as const;

type PriceMode = "above" | "below" | "move_percent";

/**
 * Add or change one alert rule. A price rule can come with its symbol fixed (from a holding's page), and shows
 * today's price so the line is easy to pick.
 */
export function RuleSheet({
  open,
  onOpenChange,
  rule,
  symbol,
  currentPrice,
  symbols = [],
  categories = [],
  save,
  onRemove,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRemove?: (rule: Rule) => void;
  rule?: Rule | null;
  symbol?: string;
  currentPrice?: number | null;
  symbols?: string[];
  categories?: { id: number; name: string }[];
  save: (rule: RuleIn, id?: number) => Promise<void>;
}) {
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={rule ? "Change alert" : symbol ? `Alert for ${symbol}` : "New alert"}
      description="Sent to your phone, at most once each time it happens. Quiet hours hold it until morning."
    >
      {open && (
        <RuleForm
          key={rule?.id ?? "new"}
          rule={rule ?? null}
          symbol={symbol}
          currentPrice={currentPrice ?? null}
          symbols={symbols}
          categories={categories}
          save={save}
          done={() => onOpenChange(false)}
        />
      )}
      {open && rule && onRemove && (
        <Button variant="ghost" className="mt-2 w-full text-negative" onClick={() => onRemove(rule)}>
          <Trash2 /> Remove this alert
        </Button>
      )}
    </Sheet>
  );
}

function RuleForm({
  rule,
  symbol: fixed,
  currentPrice,
  symbols,
  categories,
  save,
  done,
}: {
  rule: Rule | null;
  symbol?: string;
  currentPrice: number | null;
  symbols: string[];
  categories: { id: number; name: string }[];
  save: (rule: RuleIn, id?: number) => Promise<void>;
  done: () => void;
}) {
  const { state } = useAuth();
  const demo = state.status === "signed-in" && state.demo;
  const p = rule?.params ?? {};
  const [kind, setKind] = useState<Rule["kind"]>(rule?.kind ?? "price");
  const [symbol, setSymbol] = useState<string>(fixed ?? (p.symbol as string) ?? symbols[0] ?? "");
  const [mode, setMode] = useState<PriceMode>(p.below != null ? "below" : p.move_percent != null ? "move_percent" : "above");
  const [value, setValue] = useState<string>(() => {
    const v = p.above ?? p.below ?? p.move_percent ?? p.percent;
    if (v != null) return String(v);
    if (kind === "price" && currentPrice) return String(Math.round(currentPrice * 1.05));
    return "";
  });
  const [category, setCategory] = useState<string>(p.category_id != null ? String(p.category_id) : "");
  const [budgetPct, setBudgetPct] = useState<string>(
    rule?.kind === "budget" ? (p.forecast ? "forecast" : String(p.percent)) : "90",
  );
  const [concPct, setConcPct] = useState<string>(rule?.kind === "concentration" ? String(p.percent) : "10");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (demo) return toast("Alerts can't be changed while the app shows sample data.");
    let body: RuleIn;
    if (kind === "price") {
      const n = Number(value.replace(/[$,%\s]/g, ""));
      if (!symbol.trim()) return setError("Pick a symbol.");
      if (!Number.isFinite(n) || n <= 0) return setError(mode === "move_percent" ? "Enter a percentage, like 3." : "Enter a price, like 600.");
      body = { kind, symbol: symbol.trim().toUpperCase(), [mode]: n };
    } else if (kind === "budget") {
      body =
        budgetPct === "forecast"
          ? { kind, category_id: category ? Number(category) : null, forecast: true }
          : { kind, category_id: category ? Number(category) : null, percent: Number(budgetPct) };
    } else {
      const n = Number(concPct.replace(/[%\s]/g, ""));
      if (!Number.isFinite(n) || n < 1 || n > 100) return setError("Pick a share between 1% and 100%.");
      body = { kind, percent: n };
    }
    setBusy(true);
    setError(null);
    try {
      await save(body, rule?.id);
      done();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="grid grid-cols-1 gap-4">
      {!fixed && !rule && (
        <div role="radiogroup" aria-label="What to watch" className="grid grid-cols-3 gap-2">
          {(["price", "budget", "concentration"] as const).map((k) => {
            const Icon = KIND_ICON[k];
            return (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={kind === k}
                onClick={() => {
                  setKind(k);
                  setError(null);
                }}
                className={cn(
                  "flex flex-col items-center gap-1.5 rounded-2xl border px-2 py-3 text-xs font-medium transition-colors",
                  kind === k ? "border-primary bg-accent text-accent-foreground" : "hover:bg-muted",
                )}
              >
                <Icon className="size-5" aria-hidden />
                {k === "price" ? "A price" : k === "budget" ? "A budget" : "One company"}
              </button>
            );
          })}
        </div>
      )}

      {kind === "price" && (
        <>
          {!fixed && (
            <Field id="rule-symbol" label="Symbol" hint={symbols.length ? "Any ticker, not only what you hold." : undefined}>
              <Input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                list="rule-symbols"
                autoCapitalize="characters"
                autoComplete="off"
                spellCheck={false}
                maxLength={12}
                placeholder="VOO"
              />
            </Field>
          )}
          <datalist id="rule-symbols">
            {symbols.map((s) => (
              <option key={s} value={s} />
            ))}
          </datalist>
          <div role="radiogroup" aria-label="When" className="grid grid-cols-3 gap-1 rounded-full bg-muted p-1 text-sm">
            {(
              [
                ["above", "Goes above"],
                ["below", "Goes below"],
                ["move_percent", "Big day"],
              ] as const
            ).map(([m, label]) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={mode === m}
                onClick={() => {
                  setMode(m);
                  if (m === "move_percent") setValue("3");
                  else if (currentPrice) setValue(String(Math.round(currentPrice * (m === "above" ? 1.05 : 0.95))));
                }}
                className={cn("rounded-full py-2 font-medium transition-colors", mode === m ? "bg-card shadow-sm" : "text-muted-foreground")}
              >
                {label}
              </button>
            ))}
          </div>
          <Field
            id="rule-value"
            label={mode === "move_percent" ? "A move of at least" : "Price"}
            hint={
              mode === "move_percent"
                ? "Up or down from yesterday's close, once a day at most."
                : currentPrice
                  ? `Now ${currentPrice.toLocaleString("en-US", { style: "currency", currency: "USD" })}.`
                  : "In dollars."
            }
            error={error}
          >
            <Input value={value} onChange={(e) => setValue(e.target.value)} inputMode="decimal" placeholder={mode === "move_percent" ? "3" : "600"} />
          </Field>
        </>
      )}

      {kind === "budget" && (
        <>
          <Field id="rule-category" label="Budget">
            <Select value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">{budgetPct === "forecast" ? "Every category, each on its own" : "Everyday spending, all together"}</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            id="rule-budget-pct"
            label="When it"
            hint={
              budgetPct === "forecast"
                ? "A heads-up while there's still time to slow down. Once a month per category at most."
                : "Once a month at most."
            }
            error={error}
          >
            <Select value={budgetPct} onChange={(e) => setBudgetPct(e.target.value)}>
              <option value="forecast">Is on pace to go over by month-end</option>
              {[50, 75, 80, 90, 100].map((n) => (
                <option key={n} value={n}>
                  {n === 100 ? "Reaches 100% (the whole budget)" : `Reaches ${n}% of the budget`}
                </option>
              ))}
            </Select>
          </Field>
        </>
      )}

      {kind === "concentration" && (
        <Field
          id="rule-conc"
          label="When any one company is more than"
          hint="Of everything invested, counting what's inside your funds. Again only after it drops a point below."
          error={error}
        >
          <Input value={concPct} onChange={(e) => setConcPct(e.target.value)} inputMode="decimal" placeholder="10" />
        </Field>
      )}

      <Button type="submit" size="lg" disabled={busy}>
        <Bell /> {rule ? "Save" : "Add alert"}
      </Button>
    </form>
  );
}
