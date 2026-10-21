"use client";

import Link from "next/link";
import { Bell, Bot, BrainCircuit, ChevronRight, Landmark, ListChecks, LogOut, ShieldCheck, Sparkles, Users } from "lucide-react";
import { PageTitle } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth, useUser } from "@/lib/auth";
import { useApi, useMeta } from "@/lib/household";

type Release = {
  version: string;
  title: string;
  features: { id: string; title: string; body: string }[];
};
type Connections = { connections: { status: string; accounts: unknown[] }[] };

export default function Settings() {
  const meta = useMeta();
  const user = useUser();
  const { signOut, state } = useAuth();
  const { data: releases } = useApi<Release[]>("/api/whats-new");
  const { data: connections } = useApi<Connections>("/api/connections");
  const { data: guide } = useApi<{ done: number; steps: unknown[] }>("/api/guide");
  const accounts = connections?.connections.reduce((n, c) => n + c.accounts.length, 0) ?? 0;
  const stale = connections?.connections.filter((c) => c.status === "stale").length ?? 0;

  return (
    <>
      <PageTitle title="Settings" subtitle={user ? `Signed in as ${user.name}` : " "} />
      <div className="grid grid-cols-1 gap-4">
        <Card>
          <ul className="divide-y">
            <Row
              href="/settings/accounts"
              icon={<Landmark />}
              title="Accounts"
              detail={
                connections
                  ? stale
                    ? `${stale} need${stale === 1 ? "s" : ""} attention`
                    : accounts
                      ? `${accounts} linked`
                      : "Add your first account"
                  : " "
              }
              warn={stale > 0}
              tab="accounts"
            />
            <Row href="/settings/alerts" icon={<Bell />} title="Alerts" detail="Prices, budgets, the morning brief" tab="alerts" />
            <Row href="/settings/people" icon={<Users />} title="People" detail="You and your partner" tab="people" />
            <Row href="/settings/security" icon={<ShieldCheck />} title="Security" detail="Passkeys, devices, activity" tab="security" />
            <Row href="/settings/ai" icon={<BrainCircuit />} title="AI" detail="Reading files, the morning brief, providers" tab="ai" />
            <Row href="/settings/agents" icon={<Bot />} title="Agents" detail="Ask your own AI about your money" tab="agents" />
            <Row
              href="/start"
              icon={<ListChecks />}
              title="Setup guide"
              detail={guide ? `${guide.done} of ${guide.steps.length} done` : " "}
              tab="start"
            />
          </ul>
        </Card>

        <h2 className="mt-2 flex items-center gap-2 px-1 text-lg font-semibold tracking-tight">
          <Sparkles className="size-5 text-primary" aria-hidden /> What&apos;s new
        </h2>
        {releases === null && <Skeleton className="h-32 rounded-xl" />}
        {releases?.map((r) => (
          <Card key={r.version}>
            <CardHeader>
              <CardTitle className="text-base font-semibold text-foreground">{r.title}</CardTitle>
              <span className="text-xs text-muted-foreground">{r.version}</span>
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-3">
              {r.features.map((f) => (
                <div key={f.id}>
                  <p className="text-sm font-medium">{f.title}</p>
                  <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{f.body}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        ))}

        <div className="mt-2 flex items-center justify-between px-1">
          <span className="text-xs text-muted-foreground">{meta && `Version ${meta.version}`}</span>
          {state.status === "signed-in" && !state.demo && (
            <Button variant="ghost" onClick={signOut}>
              <LogOut /> Sign out
            </Button>
          )}
        </div>
      </div>
    </>
  );
}

function Row({
  href,
  icon,
  title,
  detail,
  warn,
  tab,
}: {
  href: string;
  icon: React.ReactNode;
  title: string;
  detail: string;
  warn?: boolean;
  tab: string;
}) {
  return (
    <li>
      <Link href={href} data-tab={tab} className="flex items-center gap-4 px-5 py-4 transition-colors hover:bg-muted/50">
        <span className="grid size-10 shrink-0 place-items-center rounded-full bg-accent text-accent-foreground [&_svg]:size-5" aria-hidden>
          {icon}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-medium">{title}</span>
          <span className={warn ? "block text-sm font-medium text-warning" : "block text-sm text-muted-foreground"}>{detail}</span>
        </span>
        <ChevronRight className="size-5 text-muted-foreground" aria-hidden />
      </Link>
    </li>
  );
}
