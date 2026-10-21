"use client";

import Link from "next/link";
import { useState } from "react";
import { Check, ChevronRight, Clock, Send, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

export type Step = {
  id: string;
  title: string;
  body: string;
  href: string;
  action: string;
  optional: boolean;
  done: boolean;
  waiting: string | null;
  test: boolean;
};
export type Guide = {
  steps: Step[];
  needed: number;
  needed_done: number;
  done: number;
  complete: boolean;
  hidden: boolean;
};

/** A ring that fills as steps are done, with the count inside. */
function Ring({ done, of }: { done: number; of: number }) {
  const r = 18;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative grid size-12 shrink-0 place-items-center">
      <svg viewBox="0 0 44 44" className="absolute inset-0 size-full -rotate-90" aria-hidden>
        <circle cx="22" cy="22" r={r} fill="none" stroke="var(--muted)" strokeWidth="4" />
        <circle
          cx="22"
          cy="22"
          r={r}
          fill="none"
          stroke="var(--primary)"
          strokeWidth="4"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - done / Math.max(of, 1))}
          className="transition-[stroke-dashoffset] duration-700"
        />
      </svg>
      <span className="text-xs font-semibold tabular-nums">
        {done}/{of}
      </span>
    </div>
  );
}

/** On Home until the needed steps are done (or it's hidden): how far along, and the next thing to do. */
export function GuideCard({ guide, onHide }: { guide: Guide; onHide: () => void }) {
  if (guide.complete || guide.hidden) return null;
  const next = guide.steps.find((s) => !s.optional && !s.done && !s.waiting) ?? guide.steps.find((s) => !s.optional && !s.done)!;
  return (
    <Card className="flex items-center border-primary/30 bg-accent/40 pr-2">
      <Link href="/start" className="flex min-w-0 flex-1 items-center gap-4 rounded-xl py-4 pl-5 transition-colors hover:bg-accent/60">
        <Ring done={guide.needed_done} of={guide.needed} />
        <span className="min-w-0 flex-1">
          <span className="block font-medium">Finish setting up</span>
          <span className="block text-sm text-muted-foreground">Next: {next.title}</span>
        </span>
        <ChevronRight className="size-5 shrink-0 text-primary" aria-hidden />
      </Link>
      <button
        type="button"
        onClick={onHide}
        aria-label="Hide the setup guide"
        className="grid size-10 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
      >
        <X className="size-4" aria-hidden />
      </button>
    </Card>
  );
}

/** Every step, in order: done ones ticked, the first one not done opened up with its button. */
export function GuideSteps({ guide, demo, onTested }: { guide: Guide; demo: boolean; onTested: () => void }) {
  const [testing, setTesting] = useState(false);
  const firstOpen = guide.steps.find((s) => !s.done && !s.waiting)?.id ?? null;
  const test = async () => {
    if (demo) return toast("Alerts can't be sent while the app shows sample data.");
    setTesting(true);
    try {
      const r = await api<{ channel: string }>("/api/alerts/test", {
        method: "POST",
      });
      toast(r.channel === "ntfy" ? "Sent. Check your phone." : "Sent. Check your email.");
      onTested();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setTesting(false);
    }
  };
  const groups = [
    { title: "To get going", steps: guide.steps.filter((s) => !s.optional) },
    {
      title: "Makes the numbers better",
      steps: guide.steps.filter((s) => s.optional),
    },
  ];
  let n = 0;
  return (
    <div className="grid grid-cols-1 gap-4">
      {groups.map((g) => (
        <Card key={g.title}>
          <CardHeader>
            <CardTitle>{g.title}</CardTitle>
          </CardHeader>
          <CardContent className="pt-1">
            <ol className="divide-y">
              {g.steps.map((s) => {
                n += 1;
                const open = s.id === firstOpen;
                return (
                  <li key={s.id} className="flex gap-3.5 py-3.5" aria-current={open ? "step" : undefined}>
                    <span
                      className={cn(
                        "mt-0.5 grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold",
                        s.done ? "bg-positive text-white" : open ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
                      )}
                    >
                      {s.done ? <Check className="size-4" aria-label="Done" /> : n}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className={cn("text-sm font-medium", s.done && "text-muted-foreground")}>{s.title}</p>
                      {!s.done && <p className="mt-0.5 text-sm text-muted-foreground">{s.body}</p>}
                      {!s.done && s.waiting && (
                        <p className="mt-1.5 flex items-start gap-1.5 text-xs text-warning">
                          <Clock className="mt-px size-3.5 shrink-0" aria-hidden /> {s.waiting}
                        </p>
                      )}
                      {!s.done && !s.waiting && (
                        <div className="mt-2.5 flex flex-wrap gap-2">
                          {s.test ? (
                            <>
                              <Button size="sm" variant={open ? "default" : "outline"} onClick={test} disabled={testing}>
                                <Send aria-hidden /> {s.action}
                              </Button>
                              <Button asChild size="sm" variant="ghost">
                                <Link href={s.href}>Alert settings</Link>
                              </Button>
                            </>
                          ) : (
                            <Button asChild size="sm" variant={open ? "default" : "outline"}>
                              <Link href={s.href}>
                                {s.action} <ChevronRight aria-hidden />
                              </Link>
                            </Button>
                          )}
                        </div>
                      )}
                    </div>
                    {s.done && (
                      <Link
                        href={s.href}
                        className="self-center text-sm text-muted-foreground hover:text-foreground"
                        aria-label={`${s.title}: open`}
                      >
                        <ChevronRight className="size-4" aria-hidden />
                      </Link>
                    )}
                  </li>
                );
              })}
            </ol>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
