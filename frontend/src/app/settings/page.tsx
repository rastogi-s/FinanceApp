"use client";

import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import { PageTitle } from "@/components/page";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useMeta } from "@/lib/household";

type Release = { version: string; title: string; features: { id: string; title: string; body: string }[] };

export default function Settings() {
  const meta = useMeta();
  const [releases, setReleases] = useState<Release[] | null>(null);
  useEffect(() => {
    fetch("/api/whats-new")
      .then((r) => (r.ok ? r.json() : []))
      .then(setReleases, () => setReleases([]));
  }, []);

  return (
    <>
      <PageTitle title="Settings" subtitle={meta ? `Version ${meta.version}` : " "} />
      <div className="grid gap-4">
        <Card>
          <CardHeader>
            <CardTitle>Accounts, sign-in and alerts</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Adding accounts, passkey sign-in for both of you, and alert channels (ntfy push and email) arrive in
            the next update.
          </CardContent>
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
            <CardContent className="grid gap-3">
              {r.features.map((f) => (
                <div key={f.id}>
                  <p className="text-sm font-medium">{f.title}</p>
                  <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{f.body}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        ))}
      </div>
    </>
  );
}
