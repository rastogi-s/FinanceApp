"use client";

import { useState } from "react";
import { Check, Copy, KeyRound, UserPlus, X } from "lucide-react";
import { SubPageTitle } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { useAuth, useUser } from "@/lib/auth";
import { useApi } from "@/lib/household";

type Person = { id: number; name: string; email: string; role: "owner" | "member"; passkeys: number; last_login_at: string | null };
type Data = { people: Person[]; invites: { id: number; name: string; expires_at: string }[] };
const when = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export default function PeoplePage() {
  const me = useUser();
  const { state } = useAuth();
  const demo = state.status === "signed-in" && state.demo;
  const { data, reload } = useApi<Data>("/api/auth/people");
  const [inviting, setInviting] = useState(false);
  const owner = me?.role === "owner";

  const remove = async (p: Person) => {
    try {
      await api(`/api/auth/people/${p.id}`, { method: "DELETE" });
      toast(`${p.name} can't sign in any more.`);
      reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const cancel = async (id: number) => {
    await api(`/api/auth/people/invites/${id}`, { method: "DELETE" }).catch(() => {});
    reload();
  };

  return (
    <>
      <SubPageTitle title="People" back="/settings" subtitle="Everyone here sees the whole household. Each person has their own sign-in." />
      <div className="grid grid-cols-1 gap-4">
        <Card>
          <CardHeader>
            <CardTitle>Household</CardTitle>
          </CardHeader>
          <CardContent>
            {!data ? (
              <Skeleton className="h-24 rounded-xl" />
            ) : (
              <ul className="divide-y">
                {data.people.map((p) => (
                  <li key={p.id} className="flex items-center gap-3 py-3">
                    <span
                      className="grid size-10 shrink-0 place-items-center rounded-full bg-accent font-semibold text-accent-foreground"
                      aria-hidden
                    >
                      {p.name[0]}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-2 font-medium">
                        {p.name} {p.id === me?.id && <span className="text-xs font-normal text-muted-foreground">(you)</span>}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {p.email} ·{" "}
                        {p.passkeys ? (
                          <span className="inline-flex items-center gap-1">
                            <KeyRound className="size-3" aria-hidden /> {p.passkeys} passkey{p.passkeys === 1 ? "" : "s"}
                          </span>
                        ) : (
                          "no passkey yet"
                        )}
                      </p>
                    </div>
                    {p.role === "owner" ? (
                      <Badge>Owner</Badge>
                    ) : (
                      owner && (
                        <Button variant="ghost" size="sm" className="text-negative" onClick={() => remove(p)} disabled={demo}>
                          Remove
                        </Button>
                      )
                    )}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {data && data.invites.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Waiting to join</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="divide-y">
                {data.invites.map((i) => (
                  <li key={i.id} className="flex items-center gap-3 py-3 text-sm">
                    <span className="min-w-0 flex-1">
                      <span className="font-medium">{i.name}</span>
                      <span className="text-muted-foreground"> · invitation open until {when.format(new Date(i.expires_at))}</span>
                    </span>
                    <Button variant="ghost" size="icon" aria-label={`Cancel ${i.name}'s invitation`} onClick={() => cancel(i.id)}>
                      <X />
                    </Button>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {owner && (
          <Button size="lg" variant="outline" className="justify-self-start" onClick={() => setInviting(true)} disabled={demo}>
            <UserPlus /> Invite someone
          </Button>
        )}
      </div>
      <InviteSheet open={inviting} onOpenChange={setInviting} onDone={reload} />
    </>
  );
}

function InviteSheet({ open, onOpenChange, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [invite, setInvite] = useState<{ code: string; path: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const link = invite ? `${window.location.origin}${invite.path}` : "";

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setInvite(await api<{ code: string; path: string }>("/api/auth/people/invite", { body: { name } }));
      onDone();
    } catch (err) {
      toast((err as Error).message, "error");
    }
  };
  const close = (o: boolean) => {
    if (!o) {
      setInvite(null);
      setName("");
      setCopied(false);
    }
    onOpenChange(o);
  };
  return (
    <Sheet
      open={open}
      onOpenChange={close}
      title={invite ? `Send this to ${name}` : "Invite someone"}
      description={
        invite
          ? "It works once, for two days, and only on your Tailscale network."
          : "They'll make their own password, code app and passkey."
      }
    >
      {!invite ? (
        <form onSubmit={create} className="grid grid-cols-1 gap-4 pb-2">
          <Field id="inv-name" label="Their name">
            <Input value={name} onChange={(e) => setName(e.target.value)} required />
          </Field>
          <Button size="lg" type="submit" disabled={!name.trim()}>
            Create invitation
          </Button>
        </form>
      ) : (
        <div className="grid grid-cols-1 gap-4 pb-2">
          <div className="rounded-2xl bg-muted/60 p-4">
            <p className="text-xs text-muted-foreground">Invitation link</p>
            <p className="mt-1 font-mono text-sm break-all">{link}</p>
          </div>
          <Button
            size="lg"
            onClick={() =>
              navigator.clipboard?.writeText(link).then(
                () => setCopied(true),
                () => {},
              )
            }
          >
            {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy link"}
          </Button>
        </div>
      )}
    </Sheet>
  );
}
