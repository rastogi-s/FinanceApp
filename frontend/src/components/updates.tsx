"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Check, CheckCircle2, ChevronRight, CircleAlert, Copy, LoaderCircle, RefreshCw, ShieldCheck, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/household";
import { ONE_TIME_SETUP, type ReleaseNotes, since, type Updates, useDismissed } from "@/lib/updates";
import { cn } from "@/lib/utils";

const RELEASED = /^\d+\.\d+\.\d+$/;
const POLL_MS = 2000;

/** A slim card above every page while a newer version is out. "Not now" hides it until the next one. */
export function UpdateBanner() {
  const pathname = usePathname();
  const { data, reload } = useApi<Updates>("/api/updates");
  const [dismissed, dismiss] = useDismissed();
  const [open, setOpen] = useState(false);
  // Settings has its own Updates card.
  if (!data?.available || !data.latest || pathname.startsWith("/settings")) return null;
  if (dismissed === data.latest && !open) return null;
  const headline = data.releases[0]?.title;

  return (
    <>
      <div className="update-banner mb-4 flex items-center gap-1 rounded-2xl border bg-card py-2 pr-2 pl-3 shadow-sm">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="flex min-w-0 flex-1 items-center gap-3 rounded-xl py-1 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="grid size-9 shrink-0 place-items-center rounded-full bg-primary/10 text-primary" aria-hidden>
            <Sparkles className="size-[18px]" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-medium">Version {data.latest} is ready</span>
            <span className="block truncate text-xs text-muted-foreground">{headline || "See what's new"}</span>
          </span>
          <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        </button>
        <Button variant="ghost" size="icon" className="size-9" aria-label="Not now" title="Not now" onClick={() => dismiss(data.latest!)}>
          <X />
        </Button>
      </div>
      <UpdateSheet open={open} onOpenChange={setOpen} updates={data} onChange={reload} />
    </>
  );
}

/** Settings: the running version, when it last checked, and the way into the notes and "Update now". */
export function UpdatesCard() {
  const { state } = useAuth();
  const demo = state.status === "signed-in" && state.demo;
  const { data, reload } = useApi<Updates>("/api/updates");
  const [local, setLocal] = useState<Updates | null>(null);
  const [checking, setChecking] = useState(false);
  const [open, setOpen] = useState(false);
  const u = local ?? data;
  if (!u) return <Skeleton className="h-[5.5rem] rounded-xl" />;

  const released = RELEASED.test(u.current);
  const check = async () => {
    setChecking(true);
    try {
      const next = await api<Updates>("/api/updates/check", { method: "POST" });
      setLocal(next);
      if (next.check_failed) toast("Couldn't reach GitHub. It'll try again in a few hours.", "error");
      else if (!next.available) toast("You're on the latest version.");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setChecking(false);
    }
  };

  let detail = `Version ${u.current}`;
  if (!released) detail = `Development build (${u.current})`;
  else if (!u.check_enabled) detail += " · checking is turned off on the server";
  else if (u.checked_at) detail += ` · checked ${since(u.checked_at)}${u.check_failed ? ", couldn't reach GitHub" : ""}`;

  return (
    <Card>
      <div className="flex items-center gap-4 px-5 py-4">
        <span
          className={cn(
            "grid size-10 shrink-0 place-items-center rounded-full [&_svg]:size-5",
            u.available ? "bg-primary/10 text-primary" : "bg-positive/10 text-positive",
          )}
          aria-hidden
        >
          {u.available ? <Sparkles /> : <CheckCircle2 />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium" data-testid="update-headline">
            {u.available ? `Version ${u.latest} is ready` : released ? "You're up to date" : "Updates"}
          </p>
          <p className="text-sm text-muted-foreground">{detail}</p>
        </div>
        {u.available ? (
          <Button size="sm" onClick={() => setOpen(true)}>
            See what&apos;s new
          </Button>
        ) : (
          released &&
          u.check_enabled &&
          !demo && (
            <Button variant="ghost" size="sm" onClick={check} disabled={checking}>
              <RefreshCw className={cn(checking && "animate-spin")} /> Check now
            </Button>
          )
        )}
      </div>
      {u.server.nightly && (
        <p className="border-t px-5 py-3 text-xs text-muted-foreground">New versions also install on their own every night around 4 AM.</p>
      )}
      {u.available && (
        <UpdateSheet
          open={open}
          onOpenChange={setOpen}
          updates={u}
          onChange={() => {
            setLocal(null);
            reload();
          }}
        />
      )}
    </Card>
  );
}

type Phase = "notes" | "working" | "done" | "failed";

function inProgress(u: Updates): boolean {
  return Boolean(u.requested_at) || u.status?.state === "checking" || u.status?.state === "installing";
}

/** The new version's notes, then "Update now", then the server's progress until it's back on the new version. */
export function UpdateSheet({
  open,
  onOpenChange,
  updates,
  onChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  updates: Updates;
  onChange: () => void;
}) {
  const [phase, setPhase] = useState<Phase>(() => (inProgress(updates) ? "working" : "notes"));
  const [live, setLive] = useState<Updates>(updates);
  const [askedAt, setAskedAt] = useState(() => Date.now());
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState(false);
  const target = updates.latest ?? "";

  // While the server works: ask every couple of seconds. The app restarts partway, so a failed request is expected.
  useEffect(() => {
    if (phase !== "working" || !open) return;
    let alive = true;
    const tick = async () => {
      try {
        const u = await api<Updates>("/api/updates");
        if (!alive) return;
        setOffline(false);
        setLive(u);
        if (u.current === target) {
          setPhase("done");
          return;
        }
        const s = u.status;
        const fresh = s?.at && Date.parse(s.at) >= askedAt - 60_000;
        if (fresh && (s.state === "failed" || s.state === "rolled_back" || s.state === "current")) setPhase("failed");
      } catch {
        if (alive) setOffline(true);
      }
    };
    const id = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [phase, open, askedAt, target]);

  // On the new version: load its web app.
  useEffect(() => {
    if (phase !== "done") return;
    const id = setTimeout(() => window.location.reload(), 1800);
    return () => clearTimeout(id);
  }, [phase]);

  const start = async () => {
    setBusy(true);
    try {
      const u = await api<Updates>("/api/updates/install", { method: "POST" });
      setLive(u);
      setAskedAt(Date.now());
      setPhase("working");
      onChange();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const title =
    phase === "done"
      ? `You're on ${target}`
      : phase === "working"
        ? `Updating to ${target}`
        : phase === "failed"
          ? "The update didn't go through"
          : `What's new in ${target}`;

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        // Closing never stops anything: the server carries on and the banner picks the result up.
        onOpenChange(o);
        if (!o && phase === "failed") setPhase("notes");
      }}
      title={title}
      description={phase === "notes" ? `You're on ${updates.current}.` : undefined}
    >
      {phase === "notes" && (
        <div className="grid grid-cols-1 gap-5">
          <Notes releases={updates.releases} />
          <InstallFooter updates={updates} busy={busy} onStart={start} />
        </div>
      )}
      {phase === "working" && <Progress live={live} offline={offline} target={target} />}
      {phase === "done" && (
        <div className="grid justify-items-center gap-3 py-6 text-center" role="status">
          <span className="update-done grid size-14 place-items-center rounded-full bg-positive/10 text-positive">
            <Check className="size-7" aria-hidden />
          </span>
          <p className="text-sm text-muted-foreground">Updated and healthy. Loading the new version…</p>
          <Button variant="secondary" onClick={() => window.location.reload()}>
            Reload now
          </Button>
        </div>
      )}
      {phase === "failed" && <Failed live={live} onRetry={() => setPhase("notes")} />}
    </Sheet>
  );
}

function Notes({ releases }: { releases: ReleaseNotes[] }) {
  if (!releases.length) return <p className="text-sm text-muted-foreground">This version fixes and tidies things up.</p>;
  return (
    <div className="grid grid-cols-1 gap-5">
      {releases.map((r) => (
        <section key={r.version} aria-labelledby={`notes-${r.version}`} className="grid grid-cols-1 gap-3">
          <div className="flex items-baseline gap-2">
            <h3 id={`notes-${r.version}`} className="font-semibold tracking-tight">
              {r.title || `Version ${r.version}`}
            </h3>
            <span className="ml-auto shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground tabular-nums">
              {r.version}
            </span>
          </div>
          {r.features.length > 0 && (
            <ul className="grid grid-cols-1 gap-3 border-l-2 border-primary/20 pl-4">
              {r.features.map((f) => (
                <li key={f.title}>
                  <p className="text-sm font-medium">{f.title}</p>
                  <p className="mt-0.5 text-sm leading-relaxed text-muted-foreground">{f.body}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}

function InstallFooter({ updates, busy, onStart }: { updates: Updates; busy: boolean; onStart: () => void }) {
  const nightly = updates.server.nightly ? " It also installs on its own tonight around 4 AM." : "";
  if (!updates.can_install) {
    return (
      <p className="rounded-xl bg-muted/60 p-4 text-sm text-muted-foreground">The household&apos;s owner can install it from Settings.{nightly}</p>
    );
  }
  if (!updates.server.in_app) return <SetUp nightly={nightly} />;
  return (
    <div className="sticky bottom-0 -mx-5 grid gap-3 border-t bg-card px-5 pt-4">
      <p className="flex items-start gap-2 text-xs leading-relaxed text-muted-foreground">
        <ShieldCheck className="mt-px size-4 shrink-0 text-positive" aria-hidden />
        The server checks the new version&apos;s signature and backs up first. If it doesn&apos;t start cleanly, it goes back to {updates.current} on
        its own. The app is away for about a minute.
      </p>
      <Button size="lg" onClick={onStart} disabled={busy} className="w-full">
        {busy ? <LoaderCircle className="animate-spin" /> : <Sparkles />} Update now
      </Button>
    </div>
  );
}

function SetUp({ nightly }: { nightly: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(ONE_TIME_SETUP);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast("Couldn't copy. Select the command and copy it instead.", "error");
    }
  };
  return (
    <div className="grid grid-cols-1 gap-3 rounded-xl border p-4">
      <p className="text-sm font-medium">Turn on &ldquo;Update now&rdquo;</p>
      <p className="text-sm text-muted-foreground">
        Run this once on the server. It lets the app ask the server&apos;s updater to install new versions, without giving the app any control over
        the server.{nightly}
      </p>
      <div className="flex items-center gap-2 rounded-lg bg-muted px-3 py-2">
        <code className="min-w-0 flex-1 overflow-x-auto font-mono text-xs whitespace-nowrap select-all">{ONE_TIME_SETUP}</code>
        <Button variant="ghost" size="icon" className="size-8" onClick={copy} aria-label={copied ? "Copied" : "Copy the command"}>
          {copied ? <Check /> : <Copy />}
        </Button>
      </div>
    </div>
  );
}

const STEPS = ["Asked the server", "Checking the signature", "Backing up and starting", "Back on the new version"];

function Progress({ live, offline, target }: { live: Updates; offline: boolean; target: string }) {
  const state = live.status?.state;
  // Which step is under way: the server reports checking and installing; while the app restarts it can't answer.
  let at = live.requested_at ? 0 : 1;
  if (state === "checking") at = 1;
  if (state === "installing" || offline) at = 2;
  return (
    <div className="grid grid-cols-1 gap-5 pb-2" aria-live="polite">
      <ol className="grid grid-cols-1 gap-4">
        {STEPS.map((label, i) => {
          const done = i < at;
          const active = i === at;
          return (
            <li key={label} className="flex items-center gap-3">
              <span
                className={cn(
                  "grid size-7 shrink-0 place-items-center rounded-full border text-xs transition-colors",
                  done && "border-transparent bg-positive text-white",
                  active && "border-primary text-primary",
                  !done && !active && "text-muted-foreground",
                )}
                aria-hidden
              >
                {done ? <Check className="size-4" /> : active ? <LoaderCircle className="size-4 animate-spin" /> : i + 1}
              </span>
              <span className={cn("text-sm", active ? "font-medium" : done ? "text-foreground" : "text-muted-foreground")}>
                {i === 2 ? `${label} ${target}` : label}
                <span className="sr-only">{done ? " (done)" : active ? " (under way)" : ""}</span>
              </span>
            </li>
          );
        })}
      </ol>
      {offline ? (
        <p className="text-sm text-muted-foreground">The app is restarting. This page reconnects on its own.</p>
      ) : live.stuck ? (
        <p className="flex items-start gap-2 rounded-xl bg-warning/10 p-3 text-sm text-warning">
          <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          The server hasn&apos;t picked this up yet. Check that the updater is on:{" "}
          <code className="font-mono text-xs">systemctl status financeapp-update.path</code>
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">You can close this; the update carries on.</p>
      )}
    </div>
  );
}

function Failed({ live, onRetry }: { live: Updates; onRetry: () => void }) {
  const s = live.status;
  const message =
    s?.state === "rolled_back"
      ? `${s.to ?? "The new version"} didn't start cleanly, so the server went back to ${live.current}. Nothing was lost.`
      : s?.state === "current"
        ? "The server says it's already on the newest version it can install."
        : `Nothing changed: you're still on ${live.current}.`;
  return (
    <div className="grid grid-cols-1 gap-4 pb-2" role="alert">
      <div className="flex items-start gap-3 rounded-xl bg-negative/10 p-4 text-sm">
        <CircleAlert className="mt-0.5 size-5 shrink-0 text-negative" aria-hidden />
        <div className="grid grid-cols-1 gap-1">
          <p className="font-medium">{message}</p>
          <p className="text-muted-foreground">
            What happened is in the server&apos;s log: <code className="font-mono text-xs">journalctl -u financeapp-update</code>
          </p>
        </div>
      </div>
      <Button variant="secondary" onClick={onRetry}>
        Back to what&apos;s new
      </Button>
    </div>
  );
}
