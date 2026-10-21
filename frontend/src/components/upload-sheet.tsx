"use client";

import { useRef, useState } from "react";
import { AlertTriangle, ArrowLeftRight, CheckCircle2, FileUp, Info, Sparkles } from "lucide-react";
import { type AIOffer, AIOfferCard, shrinkPicture } from "@/components/ai-offer";
import { Money } from "@/components/money";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { usd } from "@/lib/ai";
import { api } from "@/lib/api";
import { resetHousehold } from "@/lib/household";
import { cn } from "@/lib/utils";

type PreviewAccount = {
  index: number;
  institution: string;
  name: string;
  mask: string | null;
  type: string;
  holdings: number;
  holdings_cents: number;
  transactions: number;
  duplicates: number;
  first_date: string | null;
  last_date: string | null;
  balance_cents: number | null;
  file_total_cents: number | null;
  suggested_account_id: number | null;
  synced_twin: string | null;
  spent_cents: number;
  received_cents: number;
};
type Preview = {
  id: number;
  format: string;
  kind: "positions" | "transactions";
  flipped: boolean;
  warnings: string[];
  accounts: PreviewAccount[];
  targets: { id: number; name: string; type: string }[];
  ai: { model: string; cost: number } | null;
};
type Stub = {
  date: string | null;
  period_start: string | null;
  period_end: string | null;
  frequency: string | null;
  gross_cents: number | null;
  taxes_cents: number | null;
  savings_cents: number | null;
  net_cents: number | null;
  employer_match_cents: number | null;
  warnings: string[];
};
type Answer = Preview | { ai_offer: AIOffer } | { paystub: Stub; format: string; ai_cost?: number };
type Target = { account_id: number | null; name: string; institution: string; type: string; skip: boolean };

const day = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

/**
 * Upload -> check -> save. The file is read on the server and shown back as a one-screen summary ("42 transactions,
 * 3 new holdings, total matches") before anything is saved. Uploading the same file twice adds nothing. A file the
 * app can't read by itself (a PDF statement, a screenshot, a spreadsheet in another layout) comes back as an offer
 * to read it with AI, showing what would be sent; it's only sent if the person says yes.
 */
export function UploadSheet({ open, onOpenChange, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; onSaved: () => void }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [offer, setOffer] = useState<{ file: File; offer: AIOffer } | null>(null);
  const [stub, setStub] = useState<{ stub: Stub; cost?: number } | null>(null);
  const [targets, setTargets] = useState<Target[]>([]);
  const [flip, setFlip] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const reset = () => {
    setPreview(null);
    setOffer(null);
    setStub(null);
    setTargets([]);
    setFlip(false);
    setError(null);
  };

  const close = (o: boolean) => {
    if (!o) {
      if (preview) api(`/api/imports/${preview.id}`, { method: "DELETE" }).catch(() => {});
      reset();
    }
    onOpenChange(o);
  };

  const send = async (original: File, withAI = false) => {
    setError(null);
    setBusy(true);
    try {
      const file = await shrinkPicture(original);
      const form = new FormData();
      form.append("file", file);
      const answer = await api<Answer>(`/api/imports${withAI ? "?read_with_ai=true" : ""}`, { form });
      if ("ai_offer" in answer) {
        setOffer({ file, offer: answer.ai_offer });
        return;
      }
      setOffer(null);
      if ("paystub" in answer) {
        setStub({ stub: answer.paystub, cost: answer.ai_cost });
        return;
      }
      const p = answer;
      setPreview(p);
      setTargets(
        p.accounts.map((a) => ({
          account_id: a.suggested_account_id,
          name: a.name,
          institution: a.institution,
          type: a.type,
          skip: false,
        })),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!preview) return;
    setBusy(true);
    try {
      const r = await api<{ holdings: number; transactions: number; accounts: number }>(`/api/imports/${preview.id}/commit`, {
        body: { targets: targets.map((t, index) => ({ index, ...t })), flip },
      });
      const parts = [
        r.transactions ? `${r.transactions} new transaction${r.transactions === 1 ? "" : "s"}` : null,
        r.holdings ? `${r.holdings} holding${r.holdings === 1 ? "" : "s"}` : null,
      ].filter(Boolean);
      toast(parts.length ? `Saved ${parts.join(" and ")}.` : "Saved. Nothing new in that file.");
      setPreview(null);
      reset();
      onOpenChange(false);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const update = (i: number, patch: Partial<Target>) => setTargets((ts) => ts.map((t, j) => (j === i ? { ...t, ...patch } : t)));

  return (
    <Sheet
      open={open}
      onOpenChange={close}
      title={preview || stub ? "Check and save" : offer ? "Read it with AI?" : "Upload a file"}
      description={
        preview
          ? preview.format
          : stub
            ? "A pay stub"
            : offer
              ? undefined
              : "A CSV or OFX/QFX download, a spreadsheet, a PDF statement or pay stub, or a screenshot."
      }
    >
      {offer && !preview && (
        <div className="pb-2">
          <AIOfferCard offer={offer.offer} busy={busy} onAccept={() => send(offer.file, true)} onCancel={() => setOffer(null)} />
          {error && (
            <div className="mt-3">
              <ErrorLine text={error} />
            </div>
          )}
        </div>
      )}

      {stub && <StubPreview stub={stub.stub} cost={stub.cost} onCancel={() => close(false)} onSaved={() => (reset(), onOpenChange(false), onSaved())} />}

      {!preview && !offer && !stub && (
        <div className="grid grid-cols-1 gap-4 pb-2">
          <button
            type="button"
            onClick={() => input.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const f = e.dataTransfer.files[0];
              if (f) send(f);
            }}
            disabled={busy}
            className={cn(
              "flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors",
              dragging ? "border-primary bg-accent/50" : "hover:bg-muted/50",
            )}
          >
            <span className="grid size-12 place-items-center rounded-full bg-accent text-accent-foreground">
              <FileUp className="size-6" aria-hidden />
            </span>
            <span className="font-medium">{busy ? "Reading…" : "Choose a file"}</span>
            <span className="text-sm text-muted-foreground">or drop it here. Up to 5 MB.</span>
          </button>
          <input
            ref={input}
            type="file"
            accept=".csv,.ofx,.qfx,.xlsx,.pdf,text/csv,application/pdf,image/png,image/jpeg,image/webp"
            className="sr-only"
            aria-label="File to upload"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) send(f);
              e.target.value = "";
            }}
          />
          <details className="rounded-xl bg-muted/60 px-4 py-3 text-sm">
            <summary className="cursor-pointer font-medium">Where do I get one?</summary>
            <ul className="mt-2 grid gap-1.5 text-muted-foreground">
              <li>
                <b className="font-medium text-foreground">Fidelity:</b> Portfolio → Positions → Download (every account in one file).
              </li>
              <li>
                <b className="font-medium text-foreground">E*TRADE:</b> Portfolios → View: All Positions → Download.
              </li>
              <li>
                <b className="font-medium text-foreground">Banks and cards:</b> Activity → Download → CSV, or Quicken/QFX.
              </li>
              <li>
                <b className="font-medium text-foreground">Anything else:</b> a PDF statement, a spreadsheet or a screenshot. If the app
                can&apos;t read it by itself, it asks before AI reads it.
              </li>
            </ul>
          </details>
          {error && <ErrorLine text={error} />}
        </div>
      )}

      {preview && (
        <div className="grid grid-cols-1 gap-4 pb-2">
          {preview.ai && (
            <p className="flex items-start gap-2 rounded-xl bg-accent/60 px-3.5 py-3 text-sm">
              <Sparkles className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
              <span>
                Read by AI ({preview.ai.model}, {usd(preview.ai.cost)}). Check the numbers against the file before saving.
              </span>
            </p>
          )}
          {preview.accounts.map((a, i) => (
            <AccountPreview
              key={a.index}
              account={a}
              target={targets[i]!}
              targets={preview.targets}
              onChange={(patch) => update(i, patch)}
              flip={flip}
            />
          ))}
          {preview.kind === "transactions" && preview.accounts.some((a) => a.transactions > 0) && (
            <div className="rounded-2xl border p-4">
              <Switch
                checked={flip}
                onCheckedChange={setFlip}
                label="Money in and out look backwards"
                description={
                  preview.flipped
                    ? "This file lists purchases as positive amounts (most card exports do), so they were turned around."
                    : "Turn on if purchases show up as money coming in."
                }
              />
            </div>
          )}
          {preview.warnings.map((w) => (
            <p key={w} className="flex items-start gap-2 text-sm text-warning">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /> {w}
            </p>
          ))}
          {error && <ErrorLine text={error} />}
          <div className="grid grid-cols-2 gap-2 pt-1">
            <Button variant="outline" size="lg" onClick={() => close(false)} disabled={busy}>
              Cancel
            </Button>
            <Button size="lg" onClick={save} disabled={busy || targets.every((t) => t.skip)}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      )}
    </Sheet>
  );
}

function AccountPreview({
  account: a,
  target,
  targets,
  onChange,
  flip,
}: {
  account: PreviewAccount;
  target: Target;
  targets: Preview["targets"];
  onChange: (p: Partial<Target>) => void;
  flip: boolean;
}) {
  const fresh = a.transactions - a.duplicates;
  const totalOk = a.file_total_cents === null || Math.abs(a.file_total_cents - a.holdings_cents) <= 100;
  const spent = flip ? a.received_cents : a.spent_cents;
  const received = flip ? a.spent_cents : a.received_cents;
  return (
    <div className={cn("grid grid-cols-1 gap-3 rounded-2xl border p-4", target.skip && "opacity-60")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium">
            {a.institution || "Account"} {a.name && a.name !== a.institution && `· ${a.name}`}
            {a.mask && <span className="ml-1 text-muted-foreground">••{a.mask}</span>}
          </p>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {a.holdings > 0 && (
              <>
                {a.holdings} holding{a.holdings === 1 ? "" : "s"} worth <Money cents={a.holdings_cents} />
              </>
            )}
            {a.transactions > 0 && (
              <>
                {fresh} new transaction{fresh === 1 ? "" : "s"}
                {a.duplicates > 0 && `, ${a.duplicates} already here`}
                {a.first_date && a.last_date && ` · ${day.format(new Date(a.first_date))} to ${day.format(new Date(a.last_date))}`}
              </>
            )}
          </p>
        </div>
        {a.holdings > 0 && totalOk && (
          <span className="flex shrink-0 items-center gap-1 text-xs font-medium text-positive">
            <CheckCircle2 className="size-3.5" aria-hidden /> {a.file_total_cents !== null ? "Total matches" : "Looks right"}
          </span>
        )}
      </div>

      {a.transactions > 0 && (
        <div className="flex gap-4 text-sm">
          <span>
            <span className="text-muted-foreground">Out </span>
            <Money cents={spent} className="font-medium" />
          </span>
          <span>
            <span className="text-muted-foreground">In </span>
            <Money cents={received} className="font-medium" />
          </span>
          {a.balance_cents !== null && (
            <span>
              <span className="text-muted-foreground">Balance </span>
              <Money cents={a.balance_cents} className="font-medium" />
            </span>
          )}
        </div>
      )}

      {a.synced_twin && (
        <p className="flex items-start gap-2 rounded-xl bg-muted/60 p-3 text-xs text-muted-foreground">
          <ArrowLeftRight className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          {a.synced_twin} already syncs. Save this into a separate account and turn off &ldquo;Count in net worth&rdquo; on one of them, or
          skip it.
        </p>
      )}

      <div className="grid grid-cols-1 gap-2">
        <label className="text-xs font-medium text-muted-foreground" htmlFor={`target-${a.index}`}>
          Save into
        </label>
        <Select
          id={`target-${a.index}`}
          value={target.skip ? "skip" : (target.account_id ?? "new")}
          onChange={(e) => {
            const v = e.target.value;
            onChange(
              v === "skip" ? { skip: true } : v === "new" ? { skip: false, account_id: null } : { skip: false, account_id: Number(v) },
            );
          }}
        >
          <option value="new">A new account</option>
          {targets.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
          <option value="skip">Don&apos;t save this one</option>
        </Select>
        {!target.skip && target.account_id === null && (
          <div className="grid grid-cols-2 gap-2">
            <Input
              aria-label="Institution"
              placeholder="Institution"
              value={target.institution}
              onChange={(e) => onChange({ institution: e.target.value })}
            />
            <Input aria-label="Account name" placeholder="Name" value={target.name} onChange={(e) => onChange({ name: e.target.value })} />
            {a.transactions > 0 && (
              <Select
                aria-label="Kind of account"
                value={target.type}
                onChange={(e) => onChange({ type: e.target.value })}
                className="col-span-2"
              >
                <option value="depository">Checking or savings</option>
                <option value="credit">Credit card</option>
                <option value="loan">Loan</option>
              </Select>
            )}
          </div>
        )}
      </div>
      {!target.skip && target.account_id === null && a.transactions > 0 && !target.institution && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Info className="size-3.5" aria-hidden /> Name the bank so you can tell its accounts apart.
        </p>
      )}
    </div>
  );
}

function ErrorLine({ text }: { text: string }) {
  return (
    <p role="alert" className="flex items-start gap-2 text-sm font-medium text-negative">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /> {text}
    </p>
  );
}

function StubPreview({ stub, cost, onCancel, onSaved }: { stub: Stub; cost?: number; onCancel: () => void; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const missing = stub.gross_cents === null || stub.net_cents === null || stub.date === null;
  const rows: [string, number | null][] = [
    ["Gross pay", stub.gross_cents],
    ["Taxes", stub.taxes_cents],
    ["401(k), HSA, ESPP", stub.savings_cents],
    ["Take-home", stub.net_cents],
    ["Employer match", stub.employer_match_cents],
  ];
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api("/api/paychecks", {
        body: {
          date: stub.date,
          frequency: stub.frequency,
          period_start: stub.period_start,
          period_end: stub.period_end,
          gross_cents: stub.gross_cents,
          taxes_cents: stub.taxes_cents ?? 0,
          savings_cents: stub.savings_cents ?? 0,
          net_cents: stub.net_cents,
          employer_match_cents: stub.employer_match_cents ?? 0,
        },
      });
      resetHousehold();
      toast("Pay stub saved. The savings rate now uses gross pay.");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid grid-cols-1 gap-4 pb-2">
      <div className="rounded-2xl border p-4">
        <p className="font-medium">
          Paid {stub.date ? day.format(new Date(stub.date)) : "(date not found)"}
          {stub.frequency && <span className="font-normal text-muted-foreground"> · {stub.frequency}</span>}
          {cost !== undefined && <span className="font-normal text-muted-foreground"> · read by AI, {usd(cost)}</span>}
        </p>
        <dl className="mt-3 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1.5 text-sm">
          {rows.map(([label, cents]) => (
            <div key={label} className="contents">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="text-right font-medium tabular-nums">{cents === null ? "Not found" : <Money cents={cents} style="exact" />}</dd>
            </div>
          ))}
        </dl>
      </div>
      {stub.warnings.map((w) => (
        <p key={w} className="flex items-start gap-2 text-sm text-warning">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /> {w}
        </p>
      ))}
      {missing && (
        <p className="text-sm text-muted-foreground">
          Some numbers are missing. Add this stub from Budget, Pay stubs, where you can type them in.
        </p>
      )}
      {error && <ErrorLine text={error} />}
      <div className="grid grid-cols-2 gap-2">
        <Button variant="outline" size="lg" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button size="lg" onClick={save} disabled={busy || missing}>
          {busy ? "Saving…" : "Save pay stub"}
        </Button>
      </div>
    </div>
  );
}
