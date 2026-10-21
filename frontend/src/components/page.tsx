"use client";

import Link from "next/link";
import { AlertTriangle, ChevronLeft, Link2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useHousehold, type LinkedHousehold } from "@/lib/household";

export function PageTitle({ title, subtitle }: { title: string; subtitle?: React.ReactNode }) {
  return (
    <div className="mb-5">
      <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">{title}</h1>
      {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
    </div>
  );
}

/** A page inside Settings: the title with a way back. */
export function SubPageTitle({ title, subtitle, back }: { title: string; subtitle?: React.ReactNode; back: string }) {
  return (
    <div className="mb-5">
      <Link href={back} className="-ml-1 mb-2 inline-flex items-center gap-0.5 text-sm font-medium text-primary">
        <ChevronLeft className="size-4" aria-hidden /> Settings
      </Link>
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
  before,
  children,
}: {
  title: string;
  subtitle?: string;
  /** Shown above the page whether or not an account is linked yet (Home's setup guide). */
  before?: React.ReactNode;
  children: (h: LinkedHousehold) => React.ReactNode;
}) {
  const state = useHousehold();
  return (
    <>
      <PageTitle title={title} subtitle={subtitle} />
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
