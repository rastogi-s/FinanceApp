"use client";

import { useRef, useState } from "react";
import { FileUp, Sparkles } from "lucide-react";
import { AIOfferCard, shrinkPicture, type AIOffer } from "@/components/ai-offer";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toast";
import { usd } from "@/lib/ai";
import { api } from "@/lib/api";

type Answer<T> = (T & { warnings: string[]; ai_cost?: number }) | { ai_offer: AIOffer };

/** Reading a document (a letter, a W-2, a CD's receipt) into a form: pick a file, see exactly what AI would be sent
 * and say yes, then the form is filled for the person to check. Nothing is saved here and the file isn't kept.
 *
 * `box` is the dashed upload row with the offer, errors and warnings under it. Pass `{ input: false }` when the file
 * input lives elsewhere (render `input` there) so a button outside the box can open the picker with `pick`. */
export function useDocumentRead<T>(url: string, demo: boolean, onRead: (r: T) => void) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [offer, setOffer] = useState<{ file: File; offer: AIOffer } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [read, setRead] = useState<{ name: string; warnings: string[]; cost?: number } | null>(null);
  const upload = async (original: File, withAI = false) => {
    if (demo) return toast("Reading documents is off while the app shows sample data.");
    setBusy(true);
    setError(null);
    try {
      const file = await shrinkPicture(original);
      const form = new FormData();
      form.append("file", file);
      const ask = withAI ? `${url}${url.includes("?") ? "&" : "?"}read_with_ai=true` : url;
      const answer = await api<Answer<T>>(ask, { form });
      if ("ai_offer" in answer) {
        setRead(null);
        setOffer({ file, offer: answer.ai_offer });
        return;
      }
      setOffer(null);
      setRead({ name: file.name, warnings: answer.warnings, cost: answer.ai_cost });
      onRead(answer);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
      if (ref.current) ref.current.value = "";
    }
  };
  const pick = () => ref.current?.click();
  const input = (label: string) => (
    <input
      ref={ref}
      type="file"
      accept="application/pdf,.pdf,image/png,image/jpeg,image/webp"
      className="sr-only"
      aria-label={label}
      tabIndex={-1}
      onChange={(ev) => ev.target.files?.[0] && upload(ev.target.files[0])}
    />
  );
  const box = (title: string, hint: string, opts: { input?: boolean } = {}) => (
    <div className="rounded-2xl border border-dashed p-4">
      {opts.input !== false && input(title)}
      <div className="flex items-center gap-3">
        {read ? <Sparkles className="size-5 shrink-0 text-primary" aria-hidden /> : <FileUp className="size-5 shrink-0 text-primary" aria-hidden />}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{read ? `Filled in from ${read.name}` : title}</p>
          <p className="text-xs text-muted-foreground" aria-live="polite">
            {busy
              ? "Reading it…"
              : read
                ? `Check the numbers, then save.${read.cost !== undefined ? ` Read by AI, ${usd(read.cost)}.` : ""}`
                : hint}
          </p>
        </div>
        <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={pick}>
          {busy ? "Reading…" : read ? "Another" : "Upload"}
        </Button>
      </div>
      {offer && (
        <div className="mt-4">
          <AIOfferCard offer={offer.offer} busy={busy} onAccept={() => upload(offer.file, true)} onCancel={() => setOffer(null)} />
        </div>
      )}
      {error && (
        <p className="mt-3 text-sm text-negative" role="alert">
          {error}
        </p>
      )}
      {read && read.warnings.length > 0 && (
        <ul className="mt-3 grid gap-1 text-sm text-warning" role="status">
          {read.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
  return { box, input, pick, busy, reset: () => (setRead(null), setOffer(null), setError(null)) };
}
