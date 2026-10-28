"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowUp,
  ChevronRight,
  ExternalLink,
  Eye,
  EyeOff,
  Globe,
  Info,
  Minus,
  Newspaper,
  Receipt,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { usd } from "@/lib/ai";
import { api } from "@/lib/api";
import {
  type ResearchAnswer,
  type ResearchEntry,
  type ResearchOffer,
  type Sentiment,
  SENTIMENT_TEXT,
  SIDE_TITLE,
  ago,
  askPath,
} from "@/lib/research";
import { cn } from "@/lib/utils";

const MAX_QUESTION = 500;

const SENTIMENT_STYLE: Record<Sentiment, { icon: typeof TrendingUp; box: string; fg: string }> = {
  positive: { icon: TrendingUp, box: "bg-positive/10", fg: "text-positive" },
  mixed: { icon: Minus, box: "bg-warning/10", fg: "text-warning" },
  negative: { icon: TrendingDown, box: "bg-negative/10", fg: "text-negative" },
  unclear: { icon: Info, box: "bg-muted", fg: "text-muted-foreground" },
};

/** The research state for one ticker, shared by the page's card and the sheet so both show the same thread. */
export function useResearch(symbol: string) {
  // Kept with the ticker it's for, so moving to another holding never shows the last one's answers.
  const [loaded, setLoaded] = useState<{ symbol: string; offer: ResearchOffer | null; error: string | null } | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // the question being researched
  const offer = loaded?.symbol === symbol ? loaded.offer : null;
  const error = loaded?.symbol === symbol ? loaded.error : null;
  const setOffer = (f: (o: ResearchOffer | null) => ResearchOffer | null) =>
    setLoaded((l) => (l && l.symbol === symbol ? { ...l, offer: f(l.offer) } : l));

  useEffect(() => {
    if (!symbol) return;
    let live = true;
    api<ResearchOffer>(askPath(symbol)).then(
      (o) => live && setLoaded({ symbol, offer: o, error: null }),
      (e: Error) => live && setLoaded({ symbol, offer: null, error: e.message }),
    );
    return () => {
      live = false;
    };
  }, [symbol]);

  const ask = async (question: string) => {
    setBusy(question || "What's the sentiment on this, and why?");
    try {
      const r = await api<{ entry: ResearchEntry; thread: ResearchEntry[] }>(askPath(symbol), { body: { question } });
      setOffer((o) => (o ? { ...o, thread: r.thread } : o));
      return true;
    } catch (e) {
      toast((e as Error).message, "error");
      return false;
    } finally {
      setBusy(null);
    }
  };

  const clear = async () => {
    await api(askPath(symbol), { method: "DELETE" });
    setOffer((o) => (o ? { ...o, thread: [] } : o));
  };

  return { offer, error, busy, ask, clear };
}

export type Research = ReturnType<typeof useResearch>;

/** The holding page's card: the latest answer at a glance, or questions to start with. */
export function AskAICard({ symbol, r, open }: { symbol: string; r: Research; open: (question?: string) => void }) {
  const last = r.offer?.thread.at(-1);
  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>Ask AI about {symbol}</CardTitle>
        <Sparkles className="size-4 text-primary" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        {!r.offer && !r.error && <Skeleton className="h-16 rounded-xl" />}
        {r.error && <p className="text-sm text-muted-foreground">Couldn&apos;t load this: {r.error}</p>}
        {r.busy && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground" aria-live="polite">
            <Sparkles className="size-4 animate-pulse text-primary motion-reduce:animate-none" aria-hidden /> Researching &ldquo;{r.busy}&rdquo;…
          </p>
        )}
        {r.offer && last && !r.busy && (
          <button
            type="button"
            onClick={() => open()}
            className="group -mx-2 flex items-start gap-3 rounded-xl px-2 py-1.5 text-left transition-colors hover:bg-muted/50"
          >
            <SentimentDot s={last.answer.sentiment} />
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium leading-snug text-balance">{last.answer.headline}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {SENTIMENT_TEXT[last.answer.sentiment]} · {ago(last.at)}
                {last.answer.sources.length ? ` · ${last.answer.sources.length} sources` : ""}
              </span>
            </span>
            <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
          </button>
        )}
        {r.offer && !last && !r.busy && (
          <p className="text-sm text-muted-foreground">
            Sentiment, news, analyst views and the numbers, researched for you{r.offer.web_search ? " across the web" : ""}, with links to every source
            {r.offer.held ? " and what it means for your shares and taxes" : ""}.
          </p>
        )}
        {r.offer && (
          <div className="flex flex-wrap gap-1.5">
            {r.offer.suggested.slice(0, 3).map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => open(q)}
                className="rounded-full border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                {q}
              </button>
            ))}
            <Button size="sm" onClick={() => open()} className="ml-auto">
              <Sparkles /> {last ? "Ask more" : "Ask AI"}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function SentimentDot({ s }: { s: Sentiment }) {
  const st = SENTIMENT_STYLE[s];
  const Icon = st.icon;
  return (
    <span className={cn("grid size-8 shrink-0 place-items-center rounded-full", st.box, st.fg)}>
      <Icon className="size-4" aria-hidden />
    </span>
  );
}

/** The conversation: questions, researched answers with numbered sources, and a message box that stays put. */
export function AskAISheet({
  symbol,
  name,
  r,
  open,
  onOpenChange,
  start,
}: {
  symbol: string;
  name: string;
  r: Research;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  start: string | null; // a question picked on the page, asked as soon as the sheet opens
}) {
  const [question, setQuestion] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const started = useRef<string | null>(null);
  const offer = r.offer;
  const thread = offer?.thread ?? [];

  const send = async (q: string) => {
    const text = q.trim().slice(0, MAX_QUESTION);
    if (r.busy || !offer?.available) return;
    setQuestion("");
    if (await r.ask(text)) setTimeout(() => document.getElementById(`answer-${thread.length}`)?.scrollIntoView({ block: "start" }), 50);
    else setQuestion(text);
  };

  useEffect(() => {
    if (open && start && offer?.available && started.current !== start && !r.busy) {
      started.current = start;
      send(start);
    }
    if (!open) started.current = null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, start, offer?.available]);

  useEffect(() => {
    if (open) setTimeout(() => endRef.current?.scrollIntoView({ block: "end" }), 30);
  }, [open, r.busy]);

  const footer = offer ? (
    offer.available ? (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(question);
        }}
        className="grid grid-cols-1 gap-2"
      >
        {thread.length > 0 && !r.busy && (
          <div className="-mx-5 flex gap-1.5 overflow-x-auto px-5 pb-0.5 [scrollbar-width:none]">
            {offer.suggested
              .filter((q) => !thread.some((t) => t.question === q))
              .map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => send(q)}
                  className="shrink-0 rounded-full border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  {q}
                </button>
              ))}
          </div>
        )}
        <div className="flex items-end gap-2">
          <label htmlFor="ask-ai-question" className="sr-only">
            Your question about {symbol}
          </label>
          <textarea
            id="ask-ai-question"
            value={question}
            onChange={(e) => setQuestion(e.target.value.slice(0, MAX_QUESTION))}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send(question);
              }
            }}
            rows={1}
            maxLength={MAX_QUESTION}
            disabled={!!r.busy}
            placeholder={thread.length ? "Ask a follow-up…" : `Ask anything about ${symbol}…`}
            className="max-h-32 min-h-11 flex-1 resize-none rounded-2xl border bg-background px-3.5 py-2.5 text-base outline-none transition-colors [field-sizing:content] placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/20 disabled:opacity-60 md:text-sm"
          />
          <Button type="submit" size="icon" className="size-11" disabled={!!r.busy} aria-label="Ask">
            <ArrowUp />
          </Button>
        </div>
        <p className="text-[11px] leading-snug text-muted-foreground">
          To {offer.provider} ({offer.model}){offer.web_search ? ", which searches the web for the ticker and topic only" : ", from recent headlines"}.{" "}
          {offer.max_cost ? `At most ${usd(offer.max_cost)} a question, usually less.` : "Free."}
        </p>
      </form>
    ) : (
      <div className="grid grid-cols-1 gap-2">
        <p className="flex items-start gap-2 text-sm font-medium text-warning">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /> {offer.problem}
        </p>
        {offer.problem?.includes("Settings, AI") && (
          <Button asChild>
            <Link href="/settings/ai">Settings, AI</Link>
          </Button>
        )}
      </div>
    )
  ) : null;

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={`Ask AI about ${symbol}`}
      description={name && name !== symbol ? name : undefined}
      className="md:h-[min(52rem,90dvh)] md:max-w-2xl"
      footer={footer ?? <Skeleton className="h-11 rounded-2xl" />}
    >
      {!offer && (r.error ? <p className="text-sm text-muted-foreground">Couldn&apos;t load this: {r.error}</p> : <Skeleton className="h-40 rounded-2xl" />)}
      {offer && (
        <div className="grid grid-cols-1 gap-5">
          {thread.length === 0 && !r.busy && <Intro offer={offer} symbol={symbol} ask={send} />}
          {thread.map((t, i) => (
            <Exchange key={t.at} id={`answer-${i}`} t={t} />
          ))}
          {r.busy && <Researching question={r.busy} offer={offer} />}
          {thread.length > 0 && !r.busy && offer.preview !== null && (
            <div className="flex justify-center">
              <Button variant="ghost" size="sm" onClick={() => r.clear().catch((e: Error) => toast(e.message, "error"))}>
                <RotateCcw /> Start over
              </Button>
            </div>
          )}
          <Disclaimer />
          <div ref={endRef} />
        </div>
      )}
    </Sheet>
  );
}

function Intro({ offer, symbol, ask }: { offer: ResearchOffer; symbol: string; ask: (q: string) => void }) {
  return (
    <div className="grid grid-cols-1 gap-4">
      <div className="flex items-start gap-3 rounded-2xl bg-accent/60 p-4">
        <span className="grid size-10 shrink-0 place-items-center rounded-full bg-card text-primary">
          <Sparkles className="size-5" aria-hidden />
        </span>
        <p className="text-sm leading-relaxed">
          Ask about sentiment, news, analysts, the numbers or the risks. The AI reads {offer.headlines ? `${offer.headlines} recent headlines` : "the latest news"}
          {offer.web_search ? " and searches the web" : ""}, weighs both sides and links every source
          {offer.held ? ", then says what it means for your shares, gains and tax lots" : ""}.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-2">
        <p className="px-1 text-xs font-medium text-muted-foreground">Try one</p>
        {offer.suggested.map((q) => (
          <button
            key={q}
            type="button"
            disabled={!offer.available}
            onClick={() => ask(q)}
            className="flex min-h-11 items-center gap-3 rounded-xl border px-3.5 py-2.5 text-left text-sm transition-colors hover:bg-muted disabled:opacity-60"
          >
            <span className="min-w-0 flex-1">{q}</span>
            <ArrowUp className="size-4 shrink-0 rotate-45 text-muted-foreground" aria-hidden />
          </button>
        ))}
      </div>
      {offer.preview && (
        <details className="rounded-2xl border px-4 py-3 text-sm">
          <summary className="flex cursor-pointer items-center gap-2 font-medium">
            <EyeOff className="size-4 text-muted-foreground" aria-hidden /> What goes to the AI
          </summary>
          <p className="mt-2 text-muted-foreground">
            {symbol}&apos;s prices and numbers, the headlines
            {offer.held ? ", and your position: shares, value, gain, which accounts are taxable, tax lots and tax rates" : ""}. No account
            numbers or names.{offer.web_search ? " Web searches carry only the ticker, the company or fund and the topic." : ""}
          </p>
          <pre className="mt-2 max-h-64 overflow-auto rounded-xl bg-muted/60 p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words text-muted-foreground">
            {offer.preview}
          </pre>
        </details>
      )}
    </div>
  );
}

const STEPS = ["Reading the latest headlines", "Searching for analyst views and earnings", "Weighing both sides", "Checking it against your position"];

function Researching({ question, offer }: { question: string; offer: ResearchOffer }) {
  const steps = STEPS.filter((s, i) => (i !== 1 || offer.web_search) && (i !== 3 || offer.held));
  const [step, setStep] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setStep((s) => Math.min(s + 1, steps.length - 1)), 9000);
    return () => clearInterval(t);
  }, [steps.length]);
  return (
    <div className="grid grid-cols-1 gap-3" aria-live="polite" aria-busy="true">
      <Question text={question} />
      <div className="grid grid-cols-1 gap-3 rounded-2xl border p-4">
        <ol className="grid grid-cols-1 gap-2 text-sm">
          {steps.map((s, i) => (
            <li key={s} className={cn("flex items-center gap-2.5", i > step ? "text-muted-foreground/60" : i < step ? "text-muted-foreground" : "font-medium")}>
              <span
                className={cn(
                  "size-2 shrink-0 rounded-full",
                  i < step ? "bg-positive" : i === step ? "animate-pulse bg-primary motion-reduce:animate-none" : "bg-muted",
                )}
                aria-hidden
              />
              {s}
              {i === step ? "…" : ""}
            </li>
          ))}
        </ol>
        <p className="text-xs text-muted-foreground">Careful research takes a minute or two.</p>
        <Skeleton className="h-20 rounded-xl" />
      </div>
    </div>
  );
}

function Question({ text }: { text: string }) {
  return (
    <p className="ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-primary px-3.5 py-2 text-sm text-primary-foreground wrap-anywhere">{text}</p>
  );
}

function Exchange({ id, t }: { id: string; t: ResearchEntry }) {
  return (
    <article id={id} className="grid scroll-mt-2 grid-cols-1 gap-3" aria-label={`Answer to: ${t.question}`}>
      <Question text={t.question} />
      <AnswerView a={t.answer} />
      <p className="px-1 text-[11px] text-muted-foreground">
        {t.web_search ? <Globe className="mr-1 inline size-3 align-[-2px]" aria-hidden /> : <Newspaper className="mr-1 inline size-3 align-[-2px]" aria-hidden />}
        {t.web_search ? `${t.searched} web search${t.searched === 1 ? "" : "es"} and ${t.headlines} headlines` : `${t.headlines} headlines, no web search`} · {t.model} ·{" "}
        {ago(t.at)}
        {t.cost ? ` · ${usd(t.cost)}` : ""}
      </p>
    </article>
  );
}

/** Plain text only: whatever the AI writes is shown as text, never as markup. */
function plain(s: string) {
  return s.replace(/\*\*(.+?)\*\*/g, "$1");
}

function Cite({ ns, sources }: { ns: number[]; sources: ResearchAnswer["sources"] }) {
  return (
    <>
      {ns.map((n) => {
        const s = sources.find((x) => x.n === n);
        if (!s || !s.url.startsWith("https://")) return null;
        return (
          <a
            key={n}
            href={s.url}
            target="_blank"
            rel="noopener noreferrer nofollow"
            referrerPolicy="no-referrer"
            title={`${s.publisher}: ${s.title}`}
            className="ml-1 inline-grid h-4 min-w-4 place-items-center rounded-full bg-muted px-1 align-[1px] text-[10px] font-semibold text-muted-foreground no-underline tabular-nums hover:bg-primary hover:text-primary-foreground"
          >
            {n}
          </a>
        );
      })}
    </>
  );
}

function AnswerView({ a }: { a: ResearchAnswer }) {
  const st = SENTIMENT_STYLE[a.sentiment];
  const Icon = st.icon;
  const sides = (["bull", "bear", "neutral"] as const).map((side) => [side, a.points.filter((p) => p.side === side)] as const).filter(([, ps]) => ps.length);
  return (
    <div className="grid grid-cols-1 gap-3">
      <section className={cn("rounded-2xl p-4", st.box)}>
        <p className={cn("flex items-center gap-2 text-sm font-semibold", st.fg)}>
          <Icon className="size-4" aria-hidden /> {SENTIMENT_TEXT[a.sentiment]}
        </p>
        <p className="mt-1.5 text-base font-semibold leading-snug tracking-tight text-balance">{plain(a.headline)}</p>
      </section>
      <div className="grid grid-cols-1 gap-2.5 px-1 text-sm leading-relaxed">
        {a.answer
          .split(/\n\s*\n/)
          .filter((p) => p.trim())
          .map((p, i) => (
            <p key={i} className="whitespace-pre-line">
              {plain(p.trim())}
            </p>
          ))}
      </div>
      {sides.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {sides.map(([side, ps]) => (
            <div key={side} className={cn("rounded-2xl border p-3.5", side === "neutral" && sides.length > 1 && "sm:col-span-2")}>
              <p
                className={cn(
                  "mb-2 flex items-center gap-1.5 text-xs font-semibold",
                  side === "bull" ? "text-positive" : side === "bear" ? "text-negative" : "text-muted-foreground",
                )}
              >
                {side === "bull" ? <ThumbsUp className="size-3.5" aria-hidden /> : side === "bear" ? <ThumbsDown className="size-3.5" aria-hidden /> : <Info className="size-3.5" aria-hidden />}
                {SIDE_TITLE[side]}
              </p>
              <ul className="grid grid-cols-1 gap-2 text-sm leading-snug">
                {ps.map((p, i) => (
                  <li key={i}>
                    {plain(p.text)}
                    <Cite ns={p.sources} sources={a.sources} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      {a.analysts && <Section title="What analysts say">{plain(a.analysts)}</Section>}
      {a.fundamentals && <Section title="The numbers that matter">{plain(a.fundamentals)}</Section>}
      {a.for_you && (
        <section className="flex items-start gap-2.5 rounded-2xl bg-accent/60 p-3.5 text-sm">
          <Receipt className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
          <div>
            <p className="font-semibold">For your shares</p>
            <p className="mt-0.5 leading-relaxed">{plain(a.for_you)}</p>
          </div>
        </section>
      )}
      {a.watch.length > 0 && (
        <section className="px-1">
          <p className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
            <Eye className="size-3.5" aria-hidden /> Keep an eye on
          </p>
          <ul className="grid grid-cols-1 gap-1.5 text-sm">
            {a.watch.map((w) => (
              <li key={w} className="flex gap-2">
                <span className="mt-2 size-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
                {plain(w)}
              </li>
            ))}
          </ul>
        </section>
      )}
      {a.sources.length > 0 && (
        <section className="rounded-2xl border">
          <p className="px-3.5 pt-3 text-xs font-semibold text-muted-foreground">Sources ({a.sources.length})</p>
          <ol className="divide-y">
            {a.sources.map((s) =>
              s.url.startsWith("https://") ? (
                <li key={s.n}>
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer nofollow"
                    referrerPolicy="no-referrer"
                    className="flex items-start gap-3 px-3.5 py-2.5 transition-colors hover:bg-muted/50"
                  >
                    <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-muted text-[10px] font-semibold tabular-nums">{s.n}</span>
                    <span className="min-w-0 flex-1">
                      <span className="line-clamp-2 block text-sm font-medium leading-snug">{s.title}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {s.publisher}
                        {s.date ? ` · ${s.date}` : ""}
                      </span>
                    </span>
                    <ExternalLink className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  </a>
                </li>
              ) : null,
            )}
          </ol>
        </section>
      )}
      {a.caveats && (
        <p className="flex items-start gap-2 px-1 text-xs leading-relaxed text-muted-foreground">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>{plain(a.caveats)}</span>
        </p>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="px-1 text-sm">
      <p className="mb-1 text-xs font-semibold text-muted-foreground">{title}</p>
      <p className="leading-relaxed">{children}</p>
    </section>
  );
}

function Disclaimer() {
  return (
    <p className="flex items-start gap-2 px-1 text-xs leading-relaxed text-muted-foreground">
      <ShieldCheck className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <span>
        An educated take from public information, not financial advice or a prediction. AI can be wrong or out of date, and outside articles can be
        too: open the sources, check the numbers and your tax lots before you trade, and talk to a fiduciary advisor for big decisions.
      </span>
    </p>
  );
}
