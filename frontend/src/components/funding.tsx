"use client";

import { useEffect, useState } from "react";
import { Plus, Sparkles, Trash2, Wallet } from "lucide-react";
import { Money, formatMoney } from "@/components/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { type Every, type FundingIn, type FundingRow, type FundingState, type FundingSuggestion, whenLabel } from "@/lib/funding";
import { resetHousehold, useApi } from "@/lib/household";
import { cn } from "@/lib/utils";

const EVERY: [Every, string][] = [
  ["week", "Every week"],
  ["2weeks", "Every 2 weeks"],
  ["month", "Every month"],
];
const AHEAD: [number, string][] = [
  [1, "1 day"],
  [2, "2 days"],
  [3, "3 days"],
];
/** Cents as $105 or $62.40: whole dollars without the zeros. */
const fit = (cents: number) => (cents % 100 ? "exact" : "whole");
const day = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

/** The status beside a reminder: what to add, covered, or why it can't tell. */
function StateBadge({ r }: { r: FundingRow }) {
  if (r.state === "short")
    return (
      <Badge variant="warning">
        Add <Money cents={r.short_cents} style={fit(r.short_cents)} />
      </Badge>
    );
  if (r.state === "ok") return <Badge variant="positive">Covered</Badge>;
  if (r.state === "off") return <Badge variant="muted">Off</Badge>;
  return <Badge variant="muted">Cash not known</Badge>;
}

/**
 * Settings, Alerts: payments you make on a schedule (a weekly buy, a monthly move to savings), each checked against its
 * account's cash, with buys that repeat in a brokerage offered to add with one tap. `?funding=ID` opens that one.
 */
export function FundingCard({ demo, className }: { demo: boolean; className?: string }) {
  const { data } = useApi<FundingState>("/api/funding");
  const [local, setLocal] = useState<FundingState | null>(null);
  const s = local ?? data;
  const [editing, setEditing] = useState<FundingRow | null>(null);
  const [adding, setAdding] = useState<Partial<FundingIn> | null>(null);

  // A push or Home's "Needs you" opens the reminder it's about.
  useEffect(() => {
    const id = Number(new URLSearchParams(window.location.search).get("funding"));
    const found = id ? s?.reminders.find((r) => r.id === id) : undefined;
    if (found) queueMicrotask(() => setEditing(found));
  }, [s?.reminders]);

  const guard = () => {
    if (demo) toast("Reminders can't be changed while the app shows sample data.");
    return demo;
  };
  const apply = (next: FundingState) => {
    setLocal(next);
    resetHousehold();
  };
  const save = async (body: FundingIn, id?: number) => {
    apply(await api<FundingState>(id ? `/api/funding/${id}` : "/api/funding", { method: id ? "PUT" : "POST", body }));
    toast(id ? "Reminder saved." : "Reminder added. The app checks the cash before each one.");
  };
  const remove = async (r: FundingRow) => {
    if (guard()) return;
    try {
      apply(await api<FundingState>(`/api/funding/${r.id}`, { method: "DELETE" }));
      setEditing(null);
      toast("Reminder removed.");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const dismiss = async (f: FundingSuggestion) => {
    if (guard()) return;
    try {
      apply(await api<FundingState>("/api/funding/dismiss", { body: { key: f.key } }));
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <Card className={className}>
      <CardHeader>
        <div>
          <CardTitle>Low cash reminders</CardTitle>
          <p className="mt-0.5 text-xs text-muted-foreground">A payment you make on a schedule, checked against the account&apos;s cash before each one.</p>
        </div>
        <Button size="sm" onClick={() => !guard() && setAdding({})} disabled={!s}>
          <Plus /> New
        </Button>
      </CardHeader>
      {s && s.reminders.length === 0 && s.suggestions.length === 0 && (
        <p className="px-5 pb-5 text-sm text-muted-foreground">
          None yet. Add a weekly buy in your brokerage, a monthly move into savings or a loan paid from one account, and you&apos;ll get a push a day or two before
          if there isn&apos;t enough cash, with how much to move in.
        </p>
      )}
      {s && s.reminders.length > 0 && (
        <ul className="divide-y pb-2">
          {s.reminders.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => !guard() && setEditing(r)}
                className="flex w-full items-center gap-3 px-5 py-3 text-left transition-colors hover:bg-muted/60"
              >
                <span
                  className={cn(
                    "grid size-9 shrink-0 place-items-center rounded-full [&_svg]:size-4",
                    r.state === "short" ? "bg-warning/15 text-warning" : r.state === "ok" ? "bg-accent text-accent-foreground" : "bg-muted text-muted-foreground",
                  )}
                  aria-hidden
                >
                  <Wallet />
                </span>
                <span className="min-w-0 flex-1">
                  <span className={cn("block text-sm font-medium wrap-anywhere", r.state === "off" && "text-muted-foreground")}>{r.name}</span>
                  <span className="block text-xs text-muted-foreground">
                    <Money cents={r.amount_cents} style={fit(r.amount_cents)} /> · {r.schedule} · {r.account}
                  </span>
                  {r.state !== "off" && (
                    <span className="block text-xs text-muted-foreground">
                      Next {whenLabel(r.next, r.days_left)}
                      {r.cash_cents !== null && (
                        <>
                          {" "}
                          · <Money cents={r.cash_cents} style={fit(r.cash_cents)} /> cash
                        </>
                      )}
                    </span>
                  )}
                </span>
                <StateBadge r={r} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {s && s.suggestions.length > 0 && (
        <div className="mx-5 mb-5 rounded-xl border border-dashed p-3">
          <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Sparkles className="size-3.5" aria-hidden /> Found in your activity
          </p>
          <ul className="mt-1 divide-y">
            {s.suggestions.map((f) => (
              <li key={f.key} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">
                    <Money cents={f.amount_cents} style={fit(f.amount_cents)} /> {f.schedule.replace(/^Every/, "every")}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Into {f.account} · {f.times} times, last on {day.format(new Date(f.last + "T12:00:00Z"))}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1 max-sm:w-full max-sm:justify-end">
                  <Button size="sm" variant="ghost" onClick={() => dismiss(f)} aria-label={`Not a reminder: ${f.name}`}>
                    Not this
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      !guard() &&
                      setAdding({ name: f.name, account_id: f.account_id, amount_cents: f.amount_cents, every: f.every, start: f.start })
                    }
                  >
                    <Plus /> Add
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
      {s && (
        <Sheet
          open={adding !== null || editing !== null}
          onOpenChange={(o) => {
            if (!o) {
              setAdding(null);
              setEditing(null);
            }
          }}
          title={editing ? "Change reminder" : "New low cash reminder"}
          description="Before each payment the app checks the account has the cash for it. When it doesn't, Home says how much to move in, and your phone gets a push."
        >
          {(adding !== null || editing !== null) && (
            <FundingForm
              key={editing?.id ?? "new"}
              state={s}
              row={editing}
              start={adding ?? {}}
              save={save}
              done={() => {
                setAdding(null);
                setEditing(null);
              }}
            />
          )}
          {editing && (
            <Button variant="ghost" className="mt-2 w-full text-negative" onClick={() => remove(editing)}>
              <Trash2 /> Remove this reminder
            </Button>
          )}
        </Sheet>
      )}
    </Card>
  );
}

/** Next Thursday (or today, on a Thursday): the usual first answer. */
function nextThursday(today: string) {
  const d = new Date(today + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + ((4 - d.getUTCDay() + 7) % 7));
  return d.toISOString().slice(0, 10);
}

const dollars = (cents: number | undefined) => (cents ? String(cents / 100) : "");
const toCents = (v: string) => Math.round(Number(v.replace(/[$,\s]/g, "")) * 100);

function FundingForm({
  state,
  row,
  start,
  save,
  done,
}: {
  state: FundingState;
  row: FundingRow | null;
  start: Partial<FundingIn>;
  save: (body: FundingIn, id?: number) => Promise<void>;
  done: () => void;
}) {
  const first = state.accounts[0];
  const [accountId, setAccountId] = useState<number | null>(row?.account_id ?? start.account_id ?? first?.id ?? null);
  const [name, setName] = useState(row?.name ?? start.name ?? "");
  const [amount, setAmount] = useState(dollars(row?.amount_cents ?? start.amount_cents));
  const [every, setEvery] = useState<Every>(row?.every ?? start.every ?? "week");
  const [on, setOnDate] = useState(row?.next ?? start.start ?? nextThursday(state.today));
  const [cushion, setCushion] = useState(dollars(row?.cushion_cents ?? start.cushion_cents));
  const [ahead, setAhead] = useState(row?.ahead_days ?? 2);
  const [enabled, setEnabled] = useState(row?.on ?? true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const account = state.accounts.find((a) => a.id === accountId);

  if (state.accounts.length === 0) {
    return <p className="text-sm text-muted-foreground">Link a bank or brokerage account first. Credit cards and loans don&apos;t hold cash.</p>;
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cents = toCents(amount);
    if (!Number.isFinite(cents) || cents <= 0) return setError("Type the amount, like 105.");
    const extra = cushion.trim() ? toCents(cushion) : 0;
    if (!Number.isFinite(extra) || extra < 0) return setError("The cushion is a dollar amount, or leave it empty.");
    if (!accountId) return setError("Pick the account it comes out of.");
    setError(null);
    setBusy(true);
    try {
      const fallback = `${account?.name.split(" ••")[0] ?? "Account"} ${every === "week" ? "weekly" : every === "month" ? "monthly" : "every two weeks"}`;
      await save(
        { name: name.trim() || fallback, account_id: accountId, amount_cents: cents, every, start: on, cushion_cents: extra, ahead_days: ahead, on: enabled },
        row?.id,
      );
      done();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="grid grid-cols-1 gap-4">
      <Field
        id="funding-account"
        label="Comes out of"
        hint={
          account?.cash_cents != null ? (
            <>
              <Money cents={account.cash_cents} style="exact" /> cash now
              {account.cash_as_of ? ` (as of ${day.format(new Date(account.cash_as_of + "T12:00:00Z"))})` : ""}
            </>
          ) : (
            "Its cash isn't known yet: the app checks once it has a balance or holdings."
          )
        }
      >
        <Select value={accountId ?? ""} onChange={(e) => setAccountId(Number(e.target.value))}>
          {state.accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </Select>
      </Field>
      <Field id="funding-name" label="What it's for">
        <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} placeholder="E*TRADE weekly buy" />
      </Field>
      <Field id="funding-amount" label="Amount" error={error}>
        <Input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" placeholder="105" />
      </Field>
      <div className="grid grid-cols-1 gap-1.5">
        <span className="text-sm font-medium" id="funding-every">
          How often
        </span>
        <div role="radiogroup" aria-labelledby="funding-every" className="grid grid-cols-3 gap-1 rounded-full bg-muted p-1 text-sm">
          {EVERY.map(([v, label]) => (
            <button
              key={v}
              type="button"
              role="radio"
              aria-checked={every === v}
              onClick={() => setEvery(v)}
              className={cn("rounded-full py-2 font-medium transition-colors", every === v ? "bg-card shadow-sm" : "text-muted-foreground")}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <Field id="funding-date" label="Next one on" hint={every === "month" ? "Then the same day each month." : "Then the same weekday from there."}>
        <Input type="date" value={on} onChange={(e) => setOnDate(e.target.value)} required />
      </Field>
      <Field id="funding-cushion" label="Keep extra (optional)" hint="A cushion to leave in the account on top of the payment.">
        <Input value={cushion} onChange={(e) => setCushion(e.target.value)} inputMode="decimal" placeholder="0" />
      </Field>
      <div className="grid grid-cols-1 gap-1.5">
        <span className="text-sm font-medium" id="funding-ahead">
          Tell me this far ahead
        </span>
        <div role="radiogroup" aria-labelledby="funding-ahead" className="grid grid-cols-3 gap-1 rounded-full bg-muted p-1 text-sm">
          {AHEAD.map(([v, label]) => (
            <button
              key={v}
              type="button"
              role="radio"
              aria-checked={ahead === v}
              onClick={() => setAhead(v)}
              className={cn("rounded-full py-2 font-medium transition-colors", ahead === v ? "bg-card shadow-sm" : "text-muted-foreground")}
            >
              {label}
            </button>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">Bank transfers usually take a business day or two to land.</p>
      </div>
      {row && <Switch checked={enabled} onCheckedChange={setEnabled} label="Check before each one" />}
      <Button type="submit" size="lg" disabled={busy}>
        {row ? "Save" : `Add${amount && toCents(amount) > 0 ? ` ${formatMoney(toCents(amount), "exact")} ${EVERY.find(([v]) => v === every)?.[1].toLowerCase()}` : ""}`}
      </Button>
    </form>
  );
}
