"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeftRight, Check, ChevronDown, CreditCard, EyeOff, LineChart, ShieldCheck, Sparkles } from "lucide-react";
import { Money } from "@/components/money";
import { BackLink, Notice, Wide } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { usd } from "@/lib/ai";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { resetHousehold, useApi } from "@/lib/household";
import { type AISuggestion, REASONS, type ReviewGroup, type ReviewState, groupTitle } from "@/lib/review";
import { cn } from "@/lib/utils";

type Category = { id: number; name: string; kind: string };
type Offer = {
  available: boolean;
  problem: string | null;
  provider: string | null;
  model: string | null;
  max_cost: number | null;
  merchants: number;
  preview: string | null;
};

const KIND_LABEL: Record<string, string> = {
  flexible: "Everyday",
  fixed: "Bills",
  income: "Money in",
  other: "Other",
  transfer: "Between your accounts",
};
const AI_REASONS = new Set(["no_category", "guess", "money_in", "refund"]);
const day = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const sinceFmt = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", timeZone: "UTC" });

export default function ReviewPage() {
  const { data, error } = useApi<ReviewState>("/api/review");
  const { data: categories } = useApi<Category[]>("/api/categories");
  const [local, setLocal] = useState<ReviewState | null>(null);
  const [picking, setPicking] = useState<ReviewGroup | null>(null);
  const [asking, setAsking] = useState(false);
  const [ai, setAi] = useState<Record<string, AISuggestion>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const { state } = useAuth();
  const demo = state.status === "signed-in" && state.demo;
  const s = local ?? data;

  const file = async (g: ReviewGroup, c: { id: number; name: string }, remember: boolean) => {
    if (demo) {
      toast("Changing categories is off while the app shows sample data.");
      return;
    }
    setBusy(g.key);
    try {
      const r = await api<ReviewState & { also_changed: number }>("/api/review/apply", {
        method: "POST",
        body: { ids: g.ids.map(Number), category_id: c.id, remember },
      });
      setLocal(r);
      resetHousehold(true);
      const n = g.count + r.also_changed;
      toast(`Filed ${n} transaction${n === 1 ? "" : "s"} under ${c.name}.`);
      setPicking(null);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  const askable = s ? s.groups.filter((g) => AI_REASONS.has(g.reason) && !g.person).length : 0;

  return (
    <>
      <Wide />
      <div className="mb-5">
        <BackLink href="/budget">Budget</BackLink>
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Check categories</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Your budget and savings rate are only as right as these. Answer a merchant once and every transaction from it, past and future, follows.
        </p>
      </div>
      {error && !s && (
        <Notice icon={<AlertTriangle className="size-5" />} title="Couldn't load your categories">
          {error}. Check that the server is running, then reload the page.
        </Notice>
      )}
      {!s && !error && (
        <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-40 rounded-xl" />
          <Skeleton className="h-28 rounded-xl" />
          <Skeleton className="h-28 rounded-xl" />
        </div>
      )}
      {s && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Health s={s} />
          <KeptOut s={s} />
          {askable > 0 && (
            <Card className="border-primary/30 bg-accent/40 md:col-span-2">
              <CardContent className="flex items-center gap-4 pt-5">
                <Sparkles className="size-5 shrink-0 text-primary" aria-hidden />
                <p className="min-w-0 flex-1 text-sm">
                  {Object.keys(ai).length
                    ? "AI's suggestions are on the merchants below. Tap one to use it."
                    : `AI can suggest a category for ${askable} merchant${askable === 1 ? "" : "s"}. You see what goes first, and confirm each one.`}
                </p>
                {!Object.keys(ai).length && (
                  <Button size="sm" onClick={() => setAsking(true)}>
                    Suggest
                  </Button>
                )}
              </CardContent>
            </Card>
          )}
          {s.groups.length === 0 && (
            <Notice icon={<Check className="size-5" />} title="Nothing to check">
              Every transaction since {sinceFmt.format(new Date(s.since))} has a category the app is sure of.
            </Notice>
          )}
          {REASONS.map((r) => {
            const groups = s.groups.filter((g) => g.reason === r.id);
            if (!groups.length) return null;
            return (
              <section key={r.id} className="grid grid-cols-1 gap-3 md:col-span-2" aria-labelledby={`reason-${r.id}`}>
                <div className="px-1 pt-2">
                  <h2 id={`reason-${r.id}`} className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                    {r.title} · {groups.reduce((n, g) => n + g.count, 0)}
                  </h2>
                  <p className="mt-1 text-sm text-muted-foreground">{r.detail}</p>
                </div>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  {groups.map((g) => (
                    <GroupCard
                      key={g.key}
                      g={g}
                      busy={busy === g.key}
                      suggestion={ai[g.key]}
                      categories={categories ?? []}
                      onPick={() => setPicking(g)}
                      onFile={(c, remember) => file(g, c, remember)}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
      <PickSheet key={picking?.key ?? ""} group={picking} categories={categories ?? []} busy={busy !== null} onClose={() => setPicking(null)} onFile={file} />
      <Sheet
        open={asking}
        onOpenChange={setAsking}
        title="Suggest with AI"
        description="A category for each merchant the app isn't sure about. Nothing changes until you tap one."
      >
        {asking && (
          <AskFlow
            done={(sug) => {
              setAi(sug);
              setAsking(false);
            }}
          />
        )}
      </Sheet>
    </>
  );
}

function Health({ s }: { s: ReviewState }) {
  const score = s.score ?? 100;
  const tone = score >= 98 ? "text-positive" : score >= 90 ? "text-foreground" : "text-warning";
  return (
    <Card>
      <CardHeader>
        <CardTitle>Sorted with confidence</CardTitle>
        <ShieldCheck className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        <div>
          <p className={cn("text-3xl font-semibold tracking-tight tabular-nums", tone)}>{s.score === null ? "—" : `${score}%`}</p>
          <p className="mt-1 text-xs text-muted-foreground">of the money in and out since {sinceFmt.format(new Date(s.since))}</p>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-muted" role="img" aria-label={`${score}% sorted with confidence`}>
          <div className={cn("h-full rounded-full", score >= 90 ? "bg-positive" : "bg-warning")} style={{ width: `${score}%` }} />
        </div>
        <p className="text-sm">
          {s.items === 0 ? (
            "Nothing left to check."
          ) : (
            <>
              {s.items} transaction{s.items === 1 ? "" : "s"} at {s.groups.length} place{s.groups.length === 1 ? "" : "s"} to check,{" "}
              <Money cents={s.doubtful_cents} className="font-medium" /> in all.
            </>
          )}
        </p>
      </CardContent>
    </Card>
  );
}

function KeptOut({ s }: { s: ReviewState }) {
  const k = s.kept_out;
  const rows = [
    { icon: CreditCard, label: "Credit card payments", cents: k.card_cents, note: "what the cards bought already counts" },
    { icon: LineChart, label: "Into investments", cents: k.invest_cents, note: "counts as saved" },
    { icon: ArrowLeftRight, label: "Between your accounts", cents: k.own_cents, note: "your own money moving" },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Never counted as spending</CardTitle>
        <Check className="size-4 text-positive" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-2.5">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center gap-3 text-sm">
            <r.icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="block font-medium">{r.label}</span>
              <span className="block text-xs text-muted-foreground">{r.note}</span>
            </span>
            <Money cents={r.cents} className="tabular-nums" />
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function GroupCard({
  g,
  busy,
  suggestion,
  categories,
  onPick,
  onFile,
}: {
  g: ReviewGroup;
  busy: boolean;
  suggestion?: AISuggestion;
  categories: Category[];
  onPick: () => void;
  onFile: (c: { id: number; name: string }, remember: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const transfer = categories.find((c) => c.name === "Transfer");
  const current = g.category_id ? { id: g.category_id, name: g.category } : null;
  return (
    <Card className={cn("transition-opacity", busy && "opacity-60")}>
      <CardContent className="grid grid-cols-1 gap-3 pt-5">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium">{groupTitle(g)}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {g.count} transaction{g.count === 1 ? "" : "s"}, last {day.format(new Date(g.last_date))}
            </p>
          </div>
          <div className="text-right">
            <Money cents={g.total_cents} className={cn("font-semibold tabular-nums", g.total_cents > 0 && "text-positive")} />
            <div className="mt-1">
              <Badge variant={g.category_id && g.category !== "Uncategorized" ? "default" : "warning"}>{g.category}</Badge>
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="-my-1 inline-flex w-fit items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
        >
          <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} aria-hidden />
          {open ? "Hide" : "Show"} the bank&apos;s wording
        </button>
        {open && (
          <ul className="grid grid-cols-1 gap-1.5 rounded-xl bg-muted/60 px-3 py-2 text-xs">
            {g.examples.map((e, i) => (
              <li key={i} className="flex gap-2">
                <span className="w-12 shrink-0 text-muted-foreground">{day.format(new Date(e.date))}</span>
                <span className="min-w-0 flex-1 break-words">
                  {e.description} <span className="text-muted-foreground">· {e.account}</span>
                </span>
                <Money cents={e.amount_cents} style="exact" className="shrink-0 tabular-nums" />
              </li>
            ))}
          </ul>
        )}
        {suggestion && (
          <button
            type="button"
            disabled={busy}
            onClick={() => onFile({ id: suggestion.category_id, name: suggestion.category }, true)}
            className="flex items-start gap-2 rounded-xl border border-primary/40 bg-accent/40 px-3 py-2 text-left text-sm transition-colors hover:bg-accent"
          >
            <Sparkles className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="font-medium">Use {suggestion.category}</span>
              <span className="block text-xs text-muted-foreground">{suggestion.reason}</span>
            </span>
          </button>
        )}
        <div className="flex flex-wrap gap-2">
          {g.reason === "guess" && current && (
            <Button size="sm" disabled={busy} onClick={() => onFile(current, true)}>
              <Check /> {g.category} is right
            </Button>
          )}
          {g.reason === "sent" && transfer && (
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => onFile(transfer, false)}>
              <ArrowLeftRight /> My own account
            </Button>
          )}
          {g.reason === "money_in" && current && (
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => onFile(current, true)}>
              <Check /> It&apos;s income
            </Button>
          )}
          <Button size="sm" variant={g.reason === "guess" || g.reason === "sent" || g.reason === "money_in" ? "outline" : "default"} disabled={busy} onClick={onPick}>
            {g.reason === "sent" ? "It paid for something" : g.reason === "guess" ? "Change" : "Pick a category"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function PickSheet({
  group,
  categories,
  busy,
  onClose,
  onFile,
}: {
  group: ReviewGroup | null;
  categories: Category[];
  busy: boolean;
  onClose: () => void;
  onFile: (g: ReviewGroup, c: Category, remember: boolean) => void;
}) {
  const [always, setAlways] = useState(true);
  const groups = useMemo(() => {
    const kinds = group?.reason === "sent" ? ["flexible", "fixed", "other"] : ["flexible", "fixed", "income", "other", "transfer"];
    const g = new Map<string, Category[]>();
    for (const c of categories) g.set(c.kind, [...(g.get(c.kind) ?? []), c]);
    return kinds.filter((k) => g.has(k)).map((k) => [k, g.get(k)!] as const);
  }, [categories, group?.reason]);
  // A one-sided transfer that isn't to a person has no name to remember it by.
  const canRemember = group !== null && (group.reason !== "sent" || group.person !== null);
  return (
    <Sheet
      open={group !== null}
      onOpenChange={(o) => !o && onClose()}
      title={group ? groupTitle(group) : ""}
      description={
        group ? (
          <>
            {group.count} transaction{group.count === 1 ? "" : "s"}, <Money cents={group.total_cents} style="exact" />
          </>
        ) : undefined
      }
    >
      <div className="grid grid-cols-1 gap-5 pb-2">
        {groups.map(([kind, items]) => (
          <div key={kind}>
            <p className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">{KIND_LABEL[kind] ?? kind}</p>
            <div className="flex flex-wrap gap-2">
              {items.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  disabled={busy}
                  onClick={() => group && onFile(group, c, canRemember && always && c.kind !== "transfer")}
                  aria-pressed={c.id === group?.category_id}
                  className={cn(
                    "inline-flex h-10 items-center gap-1.5 rounded-full border px-4 text-sm font-medium transition",
                    c.id === group?.category_id ? "border-transparent bg-primary text-primary-foreground" : "bg-card hover:bg-muted",
                  )}
                >
                  {c.id === group?.category_id && <Check className="size-4" aria-hidden />}
                  {c.name}
                </button>
              ))}
            </div>
          </div>
        ))}
        {group && canRemember && (
          <div className="rounded-2xl border p-4">
            <Switch
              checked={always}
              onCheckedChange={setAlways}
              label={`Always for ${group.person ?? group.merchant}`}
              description={
                group.person
                  ? "Every payment to or from this person goes in the same place, now and later."
                  : "Every transaction from this merchant, at any of its stores, goes in the same place, now and later."
              }
            />
          </div>
        )}
      </div>
    </Sheet>
  );
}

function AskFlow({ done }: { done: (s: Record<string, AISuggestion>) => void }) {
  const [offer, setOffer] = useState<Offer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<Offer>("/api/review/ai").then(setOffer, (e: Error) => setError(e.message));
  }, []);
  const ask = async () => {
    setBusy(true);
    try {
      const r = await api<{ suggestions: Record<string, AISuggestion>; model: string; cost: number }>("/api/review/ai", { method: "POST" });
      const n = Object.keys(r.suggestions).length;
      toast(n ? `${n} suggestion${n === 1 ? "" : "s"} from ${r.model}, for ${usd(r.cost)}.` : "AI wasn't sure about any of them.");
      done(r.suggestions);
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(false);
    }
  };
  if (error) return <p className="text-sm text-negative">{error}</p>;
  if (!offer) return <Skeleton className="h-40 rounded-2xl" />;
  return (
    <div className="grid grid-cols-1 gap-4 pb-2">
      <div className="grid grid-cols-1 gap-2 rounded-2xl border p-4 text-sm">
        <p className="font-medium">What would be sent</p>
        <p className="flex items-start gap-2 text-muted-foreground">
          <EyeOff className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            {offer.merchants} merchant name{offer.merchants === 1 ? "" : "s"} with one line of the bank&apos;s wording each, a typical amount and
            your category names. Account numbers and your names are taken out; Zelle and Venmo payments to people aren&apos;t sent.
          </span>
        </p>
        {offer.preview && (
          <details className="rounded-xl bg-muted/60 px-3 py-2">
            <summary className="cursor-pointer text-xs font-medium">See exactly what goes</summary>
            <pre className="mt-2 max-h-56 overflow-auto font-mono text-[11px] leading-relaxed break-words whitespace-pre-wrap text-muted-foreground">
              {offer.preview}
            </pre>
          </details>
        )}
        {offer.available && (
          <p className="text-xs text-muted-foreground">
            To {offer.provider} ({offer.model}). {offer.max_cost ? `At most ${usd(offer.max_cost)}, usually much less. ` : "Free. "}
            Nothing changes until you tap a suggestion.
          </p>
        )}
      </div>
      {offer.available ? (
        <Button size="lg" onClick={ask} disabled={busy}>
          <Sparkles /> {busy ? "Looking them up…" : "Suggest categories"}
        </Button>
      ) : (
        <>
          <p className="flex items-start gap-2 text-sm font-medium text-warning">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /> {offer.problem}
          </p>
          {offer.problem && !offer.problem.startsWith("Nothing") && (
            <Button size="lg" asChild>
              <Link href="/settings/ai">Settings, AI</Link>
            </Button>
          )}
        </>
      )}
    </div>
  );
}
