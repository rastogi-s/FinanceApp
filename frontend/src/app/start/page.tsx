"use client";

import Link from "next/link";
import { PartyPopper } from "lucide-react";
import { type Guide, GuideSteps } from "@/components/guide";
import { Notice, PageTitle } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/household";

export default function Start() {
  const { state } = useAuth();
  const demo = state.status === "signed-in" && state.demo;
  const { data, error, reload } = useApi<Guide>("/api/guide");
  const show = async () => {
    try {
      await api("/api/guide", { method: "PUT", body: { hidden: false } });
      reload();
      toast("The guide is back on Home.");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <>
      <PageTitle
        title="Get set up"
        subtitle={data ? `${data.done} of ${data.steps.length} done. Each step ticks itself off once it's in place.` : " "}
      />
      {error && !data && <p className="text-sm text-negative">{error}</p>}
      {!data && !error && <Skeleton className="h-96 rounded-xl" />}
      {data && (
        <div className="grid grid-cols-1 gap-4">
          {data.complete && (
            <Notice
              icon={<PartyPopper className="size-5" />}
              title="You're set up"
              action={
                <Button asChild size="sm" className="mt-3">
                  <Link href="/">Go to Home</Link>
                </Button>
              }
            >
              Accounts sync every night, alerts reach your phone, and the budget is yours. The rest below makes the numbers sharper.
            </Notice>
          )}
          <GuideSteps guide={data} demo={demo} onTested={reload} />
          {data.hidden && !data.complete && !demo && (
            <Button variant="ghost" className="justify-self-center" onClick={show}>
              Show the guide on Home again
            </Button>
          )}
        </div>
      )}
    </>
  );
}
