"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, ChevronLeft, Link2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useHousehold, type LinkedHousehold } from "@/lib/household";
import { cameFrom } from "@/lib/nav";
import { cn } from "@/lib/utils";

export function PageTitle({ title, subtitle }: { title: string; subtitle?: React.ReactNode }) {
  return (
    <div className="mb-5">
      <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">{title}</h1>
      {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
    </div>
  );
}

/**
 * "< Portfolio" and the like. When you came from that page it goes back in history, so the page comes back
 * scrolled where you left it with its search and filters intact; otherwise it opens the page afresh.
 */
export function BackLink({ href, children }: { href: string; children: React.ReactNode }) {
  const router = useRouter();
  return (
    <Link
      href={href}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || !cameFrom(href)) return;
        e.preventDefault();
        router.back();
      }}
      className="-ml-1 mb-2 inline-flex items-center gap-0.5 text-sm font-medium text-primary"
    >
      <ChevronLeft className="size-4" aria-hidden /> {children}
    </Link>
  );
}

/** A page inside Settings: the title with a way back. */
export function SubPageTitle({ title, subtitle, back }: { title: string; subtitle?: React.ReactNode; back: string }) {
  return (
    <div className="mb-5">
      <BackLink href={back}>Settings</BackLink>
      <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">{title}</h1>
      {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
    </div>
  );
}

/**
 * Loads the household and renders the page once it's there. While loading it shows the page's shape (no
 * spinners); before any account is linked it says what to do next instead of showing an empty page.
 */
export function HouseholdPage({
  title,
  subtitle,
  heading,
  before,
  wide,
  children,
}: {
  title: string;
  subtitle?: string;
  /** Shown in place of the title (Home's greeting). */
  heading?: React.ReactNode;
  /** Lays out in columns on a laptop, so it takes the screen's width from the first frame (see Columns). */
  wide?: boolean;
  /** Shown above the page whether or not an account is linked yet (Home's setup guide). */
  before?: React.ReactNode;
  children: (h: LinkedHousehold) => React.ReactNode;
}) {
  const state = useHousehold();
  return (
    <>
      {wide && <Wide />}
      {heading ?? <PageTitle title={title} subtitle={subtitle} />}
      {before && <div className="mb-4">{before}</div>}
      {state.status === "loading" && <LoadingCards />}
      {state.status === "error" && (
        <Notice icon={<AlertTriangle className="size-5" />} title="Couldn't load your numbers">
          {state.message}. Check that the server is running, then reload the page.
        </Notice>
      )}
      {state.status === "ready" && !state.household.linked && (
        <Notice
          icon={<Link2 className="size-5" />}
          title="No accounts yet"
          action={
            <Button asChild size="sm" className="mt-3">
              <Link href="/settings/accounts">Add an account</Link>
            </Button>
          }
        >
          Link a bank, card or brokerage, or upload a file you downloaded. Your numbers show up here as soon as one is in.
        </Notice>
      )}
      {state.status === "ready" && state.household.linked && children(state.household)}
    </>
  );
}

/** Marks the page as one laid out for a laptop's width; the shell widens to fit. */
export function Wide() {
  return <span data-wide hidden />;
}

/**
 * A page's cards on a laptop: one column on a phone and a narrow window, two side by side from 1280px. Each child is
 * a Column, stacked in reading order on a phone, so the phone layout is what it always was. Full-width cards go
 * after the columns, or before them, as siblings with `xl:col-span-2`.
 */
export function Columns({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div data-wide className={cn("grid grid-cols-1 gap-4 xl:grid-cols-2 xl:items-start", className)}>
      {children}
    </div>
  );
}

/** One column of Columns. Empty columns take no space, so conditional cards don't leave a double gap. */
export function Column({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("grid min-w-0 grid-cols-1 gap-4 empty:hidden", className)}>{children}</div>;
}

function LoadingCards() {
  return (
    <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
      <Skeleton className="h-40 rounded-xl" />
      <div className="grid grid-cols-2 gap-4">
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
      </div>
      <Skeleton className="h-48 rounded-xl" />
    </div>
  );
}

export function Notice({
  icon,
  title,
  children,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <Card>
      <CardContent className="flex gap-4 pt-5">
        <div className="grid size-10 shrink-0 place-items-center rounded-full bg-accent text-accent-foreground">{icon}</div>
        <div>
          <p className="font-medium">{title}</p>
          <p className="mt-1 text-sm text-muted-foreground">{children}</p>
          {action}
        </div>
      </CardContent>
    </Card>
  );
}
