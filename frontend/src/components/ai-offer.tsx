"use client";

import Link from "next/link";
import { AlertTriangle, EyeOff, Home, ImageIcon, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { usd } from "@/lib/ai";

export type AIOffer = {
  reason: string;
  sends: "text" | "pictures";
  pages: number;
  truncated: boolean;
  preview: string | null;
  redactions: string[];
  provider: string | null;
  model: string | null;
  local: boolean;
  max_cost: number | null;
  available: boolean;
  problem: string | null;
};

/** Before anything goes to an AI provider: why it's needed, exactly what would be sent, to whom, at most what cost. */
export function AIOfferCard({
  offer,
  busy,
  onAccept,
  onCancel,
}: {
  offer: AIOffer;
  busy: boolean;
  onAccept: () => void;
  onCancel: () => void;
}) {
  const pictures = offer.sends === "pictures";
  return (
    <div className="grid grid-cols-1 gap-4">
      <div className="flex items-start gap-3 rounded-2xl bg-accent/60 p-4">
        <Sparkles className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden />
        <div className="min-w-0 text-sm">
          <p className="font-medium">{offer.reason}</p>
          <p className="mt-1 text-muted-foreground">
            {offer.available
              ? `AI can read it: ${offer.provider} (${offer.model}).`
              : "AI could read it, but it can't right now."}
          </p>
        </div>
      </div>

      {offer.available ? (
        <>
          <div className="grid grid-cols-1 gap-2 rounded-2xl border p-4 text-sm">
            <p className="font-medium">What would be sent</p>
            {pictures ? (
              <p className={offer.local ? "flex items-start gap-2 text-muted-foreground" : "flex items-start gap-2 text-warning"}>
                {offer.local ? <Home className="mt-0.5 size-4 shrink-0" aria-hidden /> : <ImageIcon className="mt-0.5 size-4 shrink-0" aria-hidden />}
                <span>
                  {offer.pages} picture{offer.pages === 1 ? "" : "s"}
                  {offer.truncated && " (the first pages only)"}, as they are.{" "}
                  {offer.local
                    ? "They stay on your network."
                    : "Details like names and account numbers can't be taken out of a picture first."}
                </span>
              </p>
            ) : (
              <>
                <p className="flex items-start gap-2 text-muted-foreground">
                  <EyeOff className="mt-0.5 size-4 shrink-0" aria-hidden />
                  <span>
                    The file&apos;s text{offer.truncated && " (the first part)"}, with{" "}
                    {offer.redactions.length ? offer.redactions.join(", ") : "nothing identifying found to"} taken out.
                  </span>
                </p>
                {offer.preview && (
                  <details className="rounded-xl bg-muted/60 px-3 py-2">
                    <summary className="cursor-pointer text-xs font-medium">See the text</summary>
                    <pre className="mt-2 max-h-56 overflow-auto font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words text-muted-foreground">
                      {offer.preview}
                    </pre>
                  </details>
                )}
              </>
            )}
            <p className="text-xs text-muted-foreground">
              {offer.max_cost ? `At most ${usd(offer.max_cost)}, usually much less. ` : "Free. "}
              Nothing is saved until you check what comes back.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Button variant="outline" size="lg" onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
            <Button size="lg" onClick={onAccept} disabled={busy}>
              <Sparkles /> {busy ? "Reading…" : "Read with AI"}
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className="flex items-start gap-2 text-sm font-medium text-warning">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /> {offer.problem}
          </p>
          <div className="grid grid-cols-2 gap-2">
            <Button variant="outline" size="lg" onClick={onCancel}>
              Back
            </Button>
            <Button size="lg" asChild>
              <Link href="/settings/ai">Settings, AI</Link>
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

/** Pictures straight from a phone can be bigger than an upload allows: scale them down (which also drops their
 * location and camera details) before they leave the device. Other files go as they are. */
export async function shrinkPicture(file: File): Promise<File> {
  if (!/^image\/(jpeg|png|webp)$/.test(file.type) || file.size < 1_500_000) return file;
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 2400 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.88));
    return blob ? new File([blob], file.name.replace(/\.\w+$/, ".jpg"), { type: "image/jpeg" }) : file;
  } catch {
    return file;
  }
}
