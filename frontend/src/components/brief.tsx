"use client";

import Link from "next/link";
import { useState } from "react";
import { Check, Sparkles, Sunrise } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { useApi } from "@/lib/household";

export type BriefRow = {
  id: number;
  day: string;
  headline: string;
  points: string[];
  written_by: string;
  channel: string | null;
  at: string;
  read: boolean;
};

export type BriefState = {
  enabled: boolean;
  time: string;
  use_ai: boolean;
  timezone: string;
  ai: { ready: boolean; model: string | null; problem: string | null };
  channel: boolean;
  today: BriefRow | null;
  latest: BriefRow | null;
};

function clock(hhmm: string) {
  const [h, m] = hhmm.split(":").map(Number);
  return new Date(2000, 0, 1, h, m).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function Points({ b }: { b: BriefRow }) {
  return (
    <ul className="grid grid-cols-1 gap-2">
      {b.points.map((p, i) => (
        <li key={i} className="flex gap-2.5 text-sm leading-relaxed">
          <span className="mt-2 size-1.5 shrink-0 rounded-full bg-primary/60" aria-hidden />
          <span>{p}</span>
        </li>
      ))}
    </ul>
  );
}

function Byline({ b }: { b: BriefRow }) {
  return (
    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
      {b.written_by === "app" ? (
        "Written by the app from your accounts"
      ) : (
        <>
          <Sparkles className="size-3.5" aria-hidden /> Worded by {b.written_by}, checked against your numbers
        </>
      )}
    </p>
  );
}

/** Today's brief on Home, until it's been read. */
export function BriefCard() {
  const { data } = useApi<BriefState>("/api/brief");
  const [hidden, setHidden] = useState(false);
  const b = data?.today;
  if (!b || b.read || hidden) return null;
  const done = () => {
    setHidden(true);
    api(`/api/brief/${b.id}/read`, { method: "POST" }).catch(() => {});
  };
  return (
    <Card className="border-primary/20 bg-accent/40">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sunrise className="size-4 text-primary" aria-hidden /> Morning brief
        </CardTitle>
        <Button variant="ghost" size="sm" onClick={done}>
          <Check /> Got it
        </Button>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        <p className="text-lg font-semibold tracking-tight">{b.headline}</p>
        <Points b={b} />
        <Byline b={b} />
      </CardContent>
    </Card>
  );
}

/** Settings, Alerts: when the brief comes, whether AI words it, and "write it now" to see one. */
export function BriefSettingsCard({ demo }: { demo: boolean }) {
  const { data, error } = useApi<BriefState>("/api/brief");
  const [local, setLocal] = useState<BriefState | null>(null);
  const [busy, setBusy] = useState(false);
  const s = local ?? data;
  if (error && !s) return null;
  if (!s) return null;

  const change = async (body: Partial<Pick<BriefState, "enabled" | "time" | "use_ai">>) => {
    if (demo) {
      toast("The brief can't be changed while the app shows sample data.");
      return;
    }
    try {
      setLocal(await api<BriefState>("/api/brief/settings", { method: "PUT", body }));
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const now = async () => {
    if (demo) {
      toast("The brief can't be written while the app shows sample data.");
      return;
    }
    setBusy(true);
    try {
      const next = await api<BriefState>("/api/brief/now", { method: "POST" });
      setLocal(next);
      toast(next.today?.channel ? "Written and sent. Check your phone." : "Written. It's on Home.");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>Morning brief</CardTitle>
        <Sunrise className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4">
        <Switch
          checked={s.enabled}
          onCheckedChange={(enabled) => change({ enabled })}
          label="A few lines every morning"
          description={`Safe to spend, yesterday's spending, anything over budget, net worth and big moves. Times in ${s.timezone.replace(/_/g, " ")}.`}
        />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field id="brief-time" label="Time">
            <Input
              type="time"
              value={s.time}
              disabled={!s.enabled}
              onChange={(e) => e.target.value && change({ time: e.target.value })}
            />
          </Field>
          <div className="self-end">
            {s.ai.ready ? (
              <Switch
                checked={s.use_ai}
                onCheckedChange={(use_ai) => change({ use_ai })}
                label="Worded by AI"
                description={`${s.ai.model}, from totals only. Any amount it makes up sends it back to the app's wording.`}
              />
            ) : (
              <div className="py-1">
                <p className="text-sm font-medium">Worded by the app</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  To have AI word it, set up &ldquo;Writing the morning brief&rdquo; in{" "}
                  <Link href="/settings/ai" className="font-medium text-primary">
                    Settings, AI
                  </Link>
                  .
                </p>
              </div>
            )}
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          {s.channel
            ? `At ${clock(s.time)} your phone gets "Your morning brief is ready", with no amounts on the lock screen. The brief itself is on Home.`
            : "No phone or email is set up, so the brief only shows on Home."}
        </p>
        {s.latest && (
          <div className="grid grid-cols-1 gap-2 rounded-2xl border p-4">
            <p className="text-xs font-medium text-muted-foreground">
              Latest, {new Date(s.latest.day + "T12:00").toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" })}
            </p>
            <p className="font-semibold">{s.latest.headline}</p>
            <Points b={s.latest} />
            <Byline b={s.latest} />
          </div>
        )}
        <Button variant="secondary" size="sm" className="justify-self-start" onClick={now} disabled={busy}>
          <Sunrise /> {busy ? "Writing…" : "Write today's now"}
        </Button>
      </CardContent>
    </Card>
  );
}
