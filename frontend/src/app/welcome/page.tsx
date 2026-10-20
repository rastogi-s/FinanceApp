"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Enrollment } from "@/components/auth/enrollment";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";

export default function WelcomePage() {
  return (
    <Suspense>
      <Welcome />
    </Suspense>
  );
}

function Welcome() {
  const code = useSearchParams().get("code") ?? "";
  const [invite, setInvite] = useState<{ name: string } | "invalid" | null>(null);
  useEffect(() => {
    if (!code) return;
    api<{ name: string }>("/api/auth/invite/check", { body: { code } }).then(setInvite, () => setInvite("invalid"));
  }, [code]);

  if (!code || invite === "invalid") return <Expired />;
  if (invite === null) return <Skeleton className="h-96 rounded-xl" />;
  return <Enrollment mode="invite" inviteCode={code} inviteName={invite.name} />;
}

function Expired() {
  return (
    <Card>
      <CardContent className="grid grid-cols-1 gap-4 pt-6">
        <h1 className="text-2xl font-semibold tracking-tight">This invitation has expired</h1>
        <p className="text-sm text-muted-foreground">Invitations work once and last two days. Ask for a new one from Settings → People.</p>
        <Button asChild variant="outline">
          <Link href="/signin">Go to sign in</Link>
        </Button>
      </CardContent>
    </Card>
  );
}
