"use client";

import { useState } from "react";
import { ArrowLeftRight } from "lucide-react";
import { Money } from "@/components/money";
import { Button } from "@/components/ui/button";
import { Field, Select } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { resetHousehold } from "@/lib/household";

type Totals = {
  transactions: number;
  spent_cents: number;
  received_cents: number;
  first_date: string | null;
  last_date: string | null;
};
type Upload = Totals & { import_id: number | null; filename: string; uploaded_at: string | null };
type FlipPreview = Totals & { account_id: number; type: string; uploads: Upload[] };

const day = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

function range(t: Totals): string {
  if (!t.first_date || !t.last_date) return "";
  const a = day.format(new Date(t.first_date));
  const b = day.format(new Date(t.last_date));
  return a === b ? a : `${a} to ${b}`;
}

/**
 * For an uploaded account whose money in and out came in the wrong way round (purchases showing as income): turns
 * every transaction around, or just one upload's, then transfers and categories are worked out again. Doing it twice
 * puts things back, so nothing here is final.
 */
export function FlipCard({
  accountId,
  name,
  disabled,
  onDone,
}: {
  accountId: number;
  name: string;
  disabled: boolean;
  onDone: () => void;
}) {
  const [preview, setPreview] = useState<FlipPreview | null>(null);
  const [pick, setPick] = useState("all");
  const [card, setCard] = useState(false);
  const [busy, setBusy] = useState(false);

  const open = async () => {
    setBusy(true);
    try {
      const p = await api<FlipPreview>(`/api/accounts/${accountId}/flip`);
      setPreview(p);
      setPick("all");
      setCard(false);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const chosen: Totals | null =
    preview && (pick === "all" ? preview : (preview.uploads.find((u) => String(u.import_id) === pick) ?? null));
  const debt = preview?.type === "credit" || preview?.type === "loan";

  const flip = async () => {
    if (!preview || !chosen) return;
    setBusy(true);
    try {
      const body: Record<string, unknown> = {};
      if (pick !== "all") body.import_id = pick === "null" ? null : Number(pick);
      if (card && !debt) body.type = "credit";
      const r = await api<{ flipped: number; removed: number }>(`/api/accounts/${accountId}/flip`, { body });
      const extra = r.removed ? ` ${r.removed} duplicate${r.removed === 1 ? " was" : "s were"} removed.` : "";
      toast(`Turned ${r.flipped} transaction${r.flipped === 1 ? "" : "s"} around.${extra}`);
      setPreview(null);
      resetHousehold();
      onDone();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-3 rounded-2xl border p-4">
      <div className="flex items-start gap-2.5">
        <ArrowLeftRight className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
        <div className="min-w-0">
          <p className="text-sm font-medium">Money in and out the wrong way round?</p>
          <p className="text-xs text-muted-foreground">
            If purchases show up as money coming in, turn them around. Transfers and categories are worked out again, and
            categories you picked yourself stay.
          </p>
        </div>
      </div>

      {!preview ? (
        <Button variant="outline" onClick={open} disabled={disabled || busy}>
          {busy ? "Looking…" : "Turn money in and out around"}
        </Button>
      ) : preview.transactions === 0 ? (
        <>
          <p className="text-sm text-muted-foreground">{name} has no transactions yet.</p>
          <Button variant="ghost" onClick={() => setPreview(null)}>
            Close
          </Button>
        </>
      ) : (
        <>
          {preview.uploads.length > 1 && (
            <Field id="flip-which" label="Which transactions">
              <Select value={pick} onChange={(e) => setPick(e.target.value)}>
                <option value="all">All {preview.transactions} transactions</option>
                {preview.uploads.map((u) => (
                  <option key={String(u.import_id)} value={String(u.import_id)}>
                    {u.filename} · {u.transactions} · {range(u)}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          {chosen && (
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div className="rounded-xl bg-muted/60 p-3">
                <p className="text-xs text-muted-foreground">Now</p>
                <p className="mt-1">
                  <span className="text-muted-foreground">Out </span>
                  <Money cents={chosen.spent_cents} className="font-medium" />
                </p>
                <p>
                  <span className="text-muted-foreground">In </span>
                  <Money cents={chosen.received_cents} className="font-medium" />
                </p>
              </div>
              <div className="rounded-xl bg-accent/60 p-3">
                <p className="text-xs text-muted-foreground">After</p>
                <p className="mt-1">
                  <span className="text-muted-foreground">Out </span>
                  <Money cents={chosen.received_cents} className="font-medium" />
                </p>
                <p>
                  <span className="text-muted-foreground">In </span>
                  <Money cents={chosen.spent_cents} className="font-medium" />
                </p>
              </div>
            </div>
          )}
          {chosen && <p className="text-xs text-muted-foreground">{range(chosen)}</p>}
          {!debt && (
            <Switch
              checked={card}
              onCheckedChange={setCard}
              label="It's a credit card"
              description="Saved as a cash account. A card's balance is what you owe, and payments to it count as card payments."
            />
          )}
          <div className="grid grid-cols-2 gap-2">
            <Button variant="outline" onClick={() => setPreview(null)} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={flip} disabled={busy || !chosen || disabled}>
              {busy ? "Turning…" : `Turn ${chosen?.transactions ?? 0} around`}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
