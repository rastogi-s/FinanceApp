"use client";

import { AlertTriangle, Link2 } from "lucide-react";
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

/**
 * Loads the household and renders the page once it's there. While loading it shows the page's shape (no
 * spinners); before any account is linked it says what to do next instead of showing an empty page.
 */
export function HouseholdPage({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: (h: LinkedHousehold) => React.ReactNode;
}) {
  const state = useHousehold();
  return (
    <>
      <PageTitle title={title} subtitle={subtitle} />
      {state.status === "loading" && <LoadingCards />}
      {state.status === "error" && (
        <Notice icon={<AlertTriangle className="size-5" />} title="Couldn't load your numbers">
          {state.message}. Check that the server is running, then reload the page.
        </Notice>
      )}
      {state.status === "ready" && !state.household.linked && (
        <Notice icon={<Link2 className="size-5" />} title="No accounts linked yet">
          Linking your bank, card and brokerage accounts arrives with sign-in in the next update. Until then the
          app is running and ready.
        </Notice>
      )}
      {state.status === "ready" && state.household.linked && children(state.household)}
    </>
  );
}

function LoadingCards() {
  return (
    <div className="grid gap-4" aria-busy="true" aria-label="Loading">
      <Skeleton className="h-40 rounded-xl" />
      <div className="grid grid-cols-2 gap-4">
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
      </div>
      <Skeleton className="h-48 rounded-xl" />
    </div>
  );
}

function Notice({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardContent className="flex gap-4 pt-5">
        <div className="grid size-10 shrink-0 place-items-center rounded-full bg-accent text-accent-foreground">{icon}</div>
        <div>
          <p className="font-medium">{title}</p>
          <p className="mt-1 text-sm text-muted-foreground">{children}</p>
        </div>
      </CardContent>
    </Card>
  );
}
