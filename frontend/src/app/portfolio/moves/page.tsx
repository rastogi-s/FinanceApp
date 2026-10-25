"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CircleCheck,
  Eye,
  EyeOff,
  Info,
  Newspaper,
  Receipt,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import { Money } from "@/components/money";
import { BackLink, Notice } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { usd } from "@/lib/ai";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  ACTION_LABEL,
  type Move,
  type MovesOffer,
  type MovesResult,
  URGENCY_LABEL,
  VERDICT_TEXT,
  actionTone,
  checkedAt,
  holdingTone,
} from "@/lib/moves";
import { hasDetail, holdingHref } from "@/lib/portfolio";
import { cn } from "@/lib/utils";

const EXAMPLES = [
  "I have $10,000 of bonus cash. Where should it go?",
  "Should I sell some of my company stock?",
  "Is my mix right for buying a house in 4 years?",
];

export default function MovesPage() {
  const { state } = useAuth();
  const demo = state.status === "signed-in" && state.demo;
  const [offer, setOffer] = useState<MovesOffer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MovesResult | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [question, setQuestion] = useState("");

  useEffect(() => {
    api<MovesOffer>("/api/portfolio/moves").then(
      (o) => {
        setOffer(o);
        setResult(o.last);
        setAsking(!o.last);
      },
      (e: Error) => setError(e.message),
    );
  }, []);

  const ask = async () => {
    setBusy(true);
    try {
      const r = await api<MovesResult>("/api/portfolio/moves", { method: "POST", body: { question } });
      setResult(r);
      setAsking(false);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const again = () => {
    setAsking(true);
    // On a phone the question box is below the answer.
    setTimeout(() => document.getElementById("moves-question")?.focus(), 50);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <>
      <BackLink href="/portfolio">Portfolio</BackLink>
      <div className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Any moves today?</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          A second opinion from AI on buying, selling or holding, from your holdings, gains, goals, taxes and today&apos;s market.
        </p>
      </div>
      {error && (
        <Notice icon={<Sparkles className="size-5" />} title="Couldn't load this">
          {error}. Check that the server is running, then reload the page.
        </Notice>
      )}
      {!offer && !error && (
        <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-40 rounded-xl" />
          <Skeleton className="h-64 rounded-xl" />
        </div>
      )}
      {offer && (
        <div className="mx-auto grid w-full max-w-3xl grid-cols-1 gap-4">
          {(asking || !result) && !busy && <Ask offer={offer} question={question} setQuestion={setQuestion} busy={busy} ask={ask} />}
          {result && !asking && !busy && (
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-1">
              <p className="min-w-0 flex-1 text-sm text-muted-foreground">
                Checked {checkedAt(result.at)} by {result.model}
                {result.cost ? ` for ${usd(result.cost)}` : ""}.
                {result.question && (
                  <>
                    {" "}You asked: <span className="text-foreground">&ldquo;{result.question}&rdquo;</span>
                  </>
                )}
                {demo && " A sample answer: with your own accounts, ask any time."}
              </p>
              <Button variant="outline" onClick={again} disabled={demo}>
                <RefreshCw /> Ask again
              </Button>
            </div>
          )}
          {busy && <Thinking holdings={offer.holdings} />}
          {!busy && result && <Result r={result} demo={demo} />}
          <Disclaimer />
        </div>
      )}
    </>
  );
}

function Ask({
  offer,
  question,
  setQuestion,
  busy,
  ask,
}: {
  offer: MovesOffer;
  question: string;
  setQuestion: (q: string) => void;
  busy: boolean;
  ask: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Ask for a review</CardTitle>
        <Sparkles className="size-4 text-primary" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4">
        <div className="grid grid-cols-1 gap-1.5">
          <label htmlFor="moves-question" className="text-sm font-medium">
            Anything on your mind? <span className="font-normal text-muted-foreground">Optional</span>
          </label>
          <textarea
            id="moves-question"
            value={question}
            onChange={(e) => setQuestion(e.target.value.slice(0, 500))}
            rows={3}
            maxLength={500}
            placeholder="e.g. I have $10,000 of bonus cash. Where should it go?"
            className="w-full resize-none rounded-xl border bg-background px-3.5 py-3 text-base outline-none transition-colors placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/20 md:text-sm"
          />
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((x) => (
              <button
                key={x}
                type="button"
                onClick={() => setQuestion(x)}
                className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                {x}
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-2 rounded-2xl border p-4 text-sm">
          <p className="font-medium">What would be sent</p>
          <p className="flex items-start gap-2 text-muted-foreground">
            <EyeOff className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span>
              Your {offer.holdings} holding{offer.holdings === 1 ? "" : "s"} by ticker with gains and which accounts are taxable, your mix,
              cash, goals and tax bracket, plus today&apos;s market
              {offer.headlines ? ` and ${offer.headlines} recent headlines` : ""}. No account numbers, names or transactions.
            </span>
          </p>
          {offer.preview && (
            <details className="rounded-xl bg-muted/60 px-3 py-2">
              <summary className="cursor-pointer text-xs font-medium">See exactly what goes</summary>
              <pre className="mt-2 max-h-72 overflow-auto font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words text-muted-foreground">
                {offer.preview}
              </pre>
            </details>
          )}
          {offer.available && (
            <p className="text-xs text-muted-foreground">
              To {offer.provider} ({offer.model}). {offer.max_cost ? `At most ${usd(offer.max_cost)}, usually much less. ` : "Free. "}
              Nothing is bought or sold.
            </p>
          )}
        </div>

        {offer.available ? (
          <Button size="lg" onClick={ask} disabled={busy}>
            <Sparkles /> {busy ? "Thinking it through…" : "Review my portfolio"}
          </Button>
        ) : (
          <>
            <p className="flex items-start gap-2 text-sm font-medium text-warning">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /> {offer.problem}
            </p>
            {offer.problem && offer.problem.includes("Settings, AI") && (
              <Button size="lg" asChild>
                <Link href="/settings/ai">Settings, AI</Link>
              </Button>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function Thinking({ holdings }: { holdings: number }) {
  return (
    <Card aria-live="polite" aria-busy="true">
      <CardContent className="grid grid-cols-1 gap-4 pt-5">
        <div className="flex items-center gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-full bg-accent text-primary">
            <Sparkles className="size-5 animate-pulse motion-reduce:animate-none" aria-hidden />
          </span>
          <div>
            <p className="font-medium">Looking at {holdings} holdings and today&apos;s market…</p>
            <p className="text-sm text-muted-foreground">A careful review takes up to a minute.</p>
          </div>
        </div>
        <Skeleton className="h-24 rounded-xl" />
        <Skeleton className="h-40 rounded-xl" />
      </CardContent>
    </Card>
  );
}

const VERDICT_STYLE = {
  hold: { icon: ShieldCheck, box: "bg-positive/10", fg: "text-positive" },
  small_moves: { icon: Sparkles, box: "bg-accent/70", fg: "text-primary" },
  act: { icon: Zap, box: "bg-warning/10", fg: "text-warning" },
} as const;

function Result({ r, demo }: { r: MovesResult; demo: boolean }) {
  const a = r.answer;
  const v = VERDICT_STYLE[a.verdict];
  const Icon = v.icon;
  return (
    <>
      <section className={cn("rounded-2xl p-5", v.box)} aria-label="Verdict">
        <p className={cn("flex items-center gap-2 text-sm font-semibold", v.fg)}>
          <Icon className="size-4" aria-hidden /> {VERDICT_TEXT[a.verdict]}
          {demo && <Badge variant="muted">Sample</Badge>}
        </p>
        <p className="mt-2 text-lg font-semibold leading-snug tracking-tight text-balance">{a.headline}</p>
        <p className="mt-2 text-sm leading-relaxed text-foreground/80">{a.summary}</p>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>The market today</CardTitle>
          <span className="text-xs text-muted-foreground">{r.market_open ? "Markets open" : "Markets closed"}</span>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3">
          {a.indexes.length > 0 && (
            <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3" aria-label="Indexes">
              {a.indexes.map((i) => (
                <li key={i.name} className="min-w-0 rounded-xl bg-muted/50 px-3 py-2">
                  <p className="truncate text-xs text-muted-foreground">{i.name}</p>
                  <p className="flex items-baseline justify-between gap-2 tabular-nums">
                    <span className="text-sm font-medium">{i.level.toLocaleString("en-US", { maximumFractionDigits: 2 })}</span>
                    {i.change_percent !== undefined && (
                      <span className={cn("text-xs font-medium", i.change_percent >= 0 ? "text-positive" : "text-negative")}>
                        {i.change_percent >= 0 ? "+" : ""}
                        {i.change_percent.toFixed(2)}%
                      </span>
                    )}
                  </p>
                </li>
              ))}
            </ul>
          )}
          <p className="text-sm leading-relaxed">{a.market}</p>
          {a.headline_count > 0 && (
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Newspaper className="size-3.5" aria-hidden /> Weighed {a.headline_count} recent headlines on your largest holdings.
            </p>
          )}
        </CardContent>
      </Card>

      <section aria-labelledby="moves-title" className="grid grid-cols-1 gap-3">
        <h2 id="moves-title" className="px-1 text-sm font-medium text-muted-foreground">
          {a.moves.length ? `Ideas to consider (${a.moves.length})` : "Ideas to consider"}
        </h2>
        {a.moves.length === 0 ? (
          <Card>
            <CardContent className="flex items-start gap-3 pt-5">
              <CircleCheck className="mt-0.5 size-5 shrink-0 text-positive" aria-hidden />
              <p className="text-sm">No moves suggested. Staying the course is a decision too.</p>
            </CardContent>
          </Card>
        ) : (
          a.moves.map((m, i) => <MoveCard key={`${m.symbol}-${i}`} m={m} />)
        )}
      </section>

      {a.holdings.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Holding by holding</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {a.holdings.map((h) => (
                <li key={h.symbol} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                  <div className="w-20 shrink-0">
                    {hasDetail(h.symbol) ? (
                      <Link href={holdingHref(h.symbol)} className="block truncate font-medium hover:underline" title={h.symbol}>
                        {h.symbol}
                      </Link>
                    ) : (
                      <span className="block truncate font-medium" title={h.symbol}>
                        {h.symbol}
                      </span>
                    )}
                    {h.percent !== null && <span className="text-xs text-muted-foreground tabular-nums">{h.percent.toFixed(1)}%</span>}
                  </div>
                  <p className="min-w-0 flex-1 text-sm leading-relaxed text-muted-foreground">{h.note}</p>
                  <Badge variant={holdingTone(h.verdict)} className="shrink-0 capitalize">
                    {h.verdict}
                  </Badge>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {a.watch.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Keep an eye on</CardTitle>
            <Eye className="size-4 text-muted-foreground" aria-hidden />
          </CardHeader>
          <CardContent>
            <ul className="grid grid-cols-1 gap-2 text-sm">
              {a.watch.map((w) => (
                <li key={w} className="flex gap-2">
                  <span className="mt-2 size-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
                  {w}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {a.caveats && (
        <p className="flex items-start gap-2 px-1 text-xs leading-relaxed text-muted-foreground">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>What it couldn&apos;t see: {a.caveats}</span>
        </p>
      )}
    </>
  );
}

function MoveCard({ m }: { m: Move }) {
  const tone = actionTone(m.action);
  const Arrow = tone === "positive" ? ArrowUpRight : tone === "negative" ? ArrowDownRight : RefreshCw;
  return (
    <Card>
      <CardContent className="grid grid-cols-1 gap-3 pt-5">
        <div className="flex items-start gap-3">
          <span
            className={cn(
              "grid size-10 shrink-0 place-items-center rounded-full",
              tone === "positive" ? "bg-positive/10 text-positive" : tone === "negative" ? "bg-negative/10 text-negative" : "bg-accent text-primary",
            )}
          >
            <Arrow className="size-5" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <p className="flex flex-wrap items-baseline gap-x-1.5 font-semibold">
              <span>{ACTION_LABEL[m.action]}</span>
              {m.held && hasDetail(m.symbol) ? (
                <Link href={holdingHref(m.symbol)} className="hover:underline">
                  {m.symbol}
                </Link>
              ) : (
                <span>{m.symbol}</span>
              )}
              {m.amount_cents > 0 && (
                <span className="font-normal text-muted-foreground">
                  · about <Money cents={m.amount_cents} />
                </span>
              )}
            </p>
            <p className="line-clamp-2 text-sm text-muted-foreground">
              {m.name ?? (m.held ? "" : "Not held yet")}
              {m.account ? `${m.name || !m.held ? " · " : ""}in ${m.account}` : ""}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge variant={m.urgency === "today" ? "warning" : "muted"}>{URGENCY_LABEL[m.urgency]}</Badge>
          <Badge variant="muted">{m.confidence.charAt(0).toUpperCase() + m.confidence.slice(1)} confidence</Badge>
        </div>
        <p className="text-sm leading-relaxed">{m.why}</p>
        {m.tax_impact && (
          <p className="flex items-start gap-2 rounded-xl bg-muted/50 px-3 py-2 text-sm">
            <Receipt className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
            <span>
              <span className="font-medium">Taxes: </span>
              {m.tax_impact}
            </span>
          </p>
        )}
        {m.risks && (
          <p className="flex items-start gap-2 px-1 text-sm text-muted-foreground">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span>
              <span className="font-medium text-foreground">What could go wrong: </span>
              {m.risks}
            </span>
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function Disclaimer() {
  return (
    <p className="flex items-start gap-2 px-1 text-xs leading-relaxed text-muted-foreground">
      <ShieldCheck className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <span>
        Ideas to think about, not financial advice. The app never buys or sells anything, and AI can be wrong. Check the numbers and your tax
        lots before you trade, and talk to a fiduciary advisor for big decisions.
      </span>
    </p>
  );
}
