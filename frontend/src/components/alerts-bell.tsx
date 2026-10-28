"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Bell, BellOff, ChevronRight, SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { type Alert, KIND_DOT, alertHref, resetHousehold } from "@/lib/household";
import { cn } from "@/lib/utils";

type Inbox = { events: Alert[]; unread: number };

const time = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });
const day = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric" });

function when(iso: string) {
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString() ? time.format(d) : day.format(d);
}

/**
 * The bell in the top bar: a dot with the count of unread alerts, and a sheet with the last month's alerts.
 * Tapping one opens the page it's about and marks it read.
 */
export function AlertsBell({ demo, className }: { demo: boolean; className?: string }) {
  const pathname = usePathname();
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(() => {
    api<Inbox>("/api/alerts/inbox").then(setInbox, () => {});
  }, []);

  // Fresh on every page, when the app comes back to the front, after a refresh, and after alerts are read elsewhere (Home's Clear).
  useEffect(load, [load, pathname]);
  useEffect(() => {
    const visible = () => document.visibilityState === "visible" && load();
    document.addEventListener("visibilitychange", visible);
    window.addEventListener("app-refresh", load);
    window.addEventListener("household-reset", load);
    return () => {
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener("app-refresh", load);
      window.removeEventListener("household-reset", load);
    };
  }, [load]);

  const unread = inbox?.unread ?? 0;

  const markRead = async (ids?: string[]) => {
    if (demo) {
      if (!ids) toast("Alerts stay put while the app shows sample data.");
      return;
    }
    // Shown read right away; the server catches up.
    setInbox((i) =>
      i && {
        events: i.events.map((e) => (!ids || ids.includes(e.id) ? { ...e, read: true } : e)),
        unread: ids ? Math.max(0, i.unread - i.events.filter((e) => ids.includes(e.id) && !e.read).length) : 0,
      },
    );
    try {
      await api("/api/alerts/read", { method: "POST", body: ids ? { ids } : {} });
      resetHousehold(true); // Home's Alerts card lists the unread ones
    } catch (e) {
      toast((e as Error).message, "error");
      load();
    }
  };

  const label = unread ? `Alerts, ${unread} unread` : "Alerts";
  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className={cn("relative", className)}
        onClick={() => {
          setOpen(true);
          load();
        }}
        aria-label={label}
        aria-haspopup="dialog"
        title={label}
        data-tab="alerts-bell"
      >
        <Bell />
        {unread > 0 && (
          <span
            className="absolute top-1 right-1 grid h-[18px] min-w-[18px] place-items-center rounded-full bg-negative px-1 text-[10px] leading-none font-semibold text-white tabular-nums ring-2 ring-background"
            aria-hidden
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </Button>

      <Sheet
        open={open}
        onOpenChange={setOpen}
        title="Alerts"
        description={unread ? `${unread} unread from the last month` : "The last month's alerts"}
      >
        <div className="flex items-center justify-between gap-2 pb-1">
          <Button variant="secondary" size="sm" onClick={() => markRead()} disabled={!unread}>
            Mark all read
          </Button>
          <Button variant="ghost" size="sm" asChild>
            <Link href="/settings/alerts" onClick={() => setOpen(false)}>
              <SlidersHorizontal /> Alert rules
            </Link>
          </Button>
        </div>

        {!inbox ? (
          <div className="grid gap-3 py-3" aria-busy="true" aria-label="Loading alerts">
            <Skeleton className="h-12 rounded-lg" />
            <Skeleton className="h-12 rounded-lg" />
            <Skeleton className="h-12 rounded-lg" />
          </div>
        ) : inbox.events.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-10 text-center">
            <span className="grid size-12 place-items-center rounded-full bg-muted text-muted-foreground" aria-hidden>
              <BellOff className="size-5" />
            </span>
            <p className="font-medium">Nothing in the last month</p>
            <p className="max-w-xs text-sm text-muted-foreground">
              Price moves, dips, budgets and goals you set rules for show up here and on your phone.
            </p>
          </div>
        ) : (
          <ul className="divide-y" aria-label="Recent alerts">
            {inbox.events.map((a) => {
              const href = alertHref(a);
              const row = (
                <>
                  <span
                    className={cn("mt-1.5 size-2 shrink-0 rounded-full", a.read ? "bg-muted-foreground/30" : KIND_DOT[a.kind])}
                    aria-hidden
                  />
                  <div className="min-w-0 flex-1">
                    <p className={cn("text-sm wrap-anywhere", a.read ? "text-muted-foreground" : "font-medium")}>
                      {!a.read && <span className="sr-only">Unread: </span>}
                      {a.title}
                    </p>
                    {a.body && <p className="text-sm text-muted-foreground wrap-anywhere">{a.body}</p>}
                  </div>
                  <time dateTime={a.at} className="shrink-0 text-xs text-muted-foreground">
                    {when(a.at)}
                  </time>
                  {href && <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />}
                </>
              );
              return (
                <li key={a.id}>
                  {href ? (
                    <Link
                      href={href}
                      onClick={() => {
                        setOpen(false);
                        if (!a.read) markRead([a.id]);
                      }}
                      className="-mx-2 flex min-h-11 items-start gap-3 rounded-lg px-2 py-3 transition-colors hover:bg-muted/60"
                    >
                      {row}
                    </Link>
                  ) : (
                    <div className="flex items-start gap-3 py-3">{row}</div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Sheet>
    </>
  );
}
