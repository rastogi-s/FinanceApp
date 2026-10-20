"use client";

import { useEffect, useSyncExternalStore } from "react";
import { CheckCircle2, CircleAlert } from "lucide-react";
import { cn } from "@/lib/utils";

type Toast = { id: number; text: string; tone: "ok" | "error" };
let toasts: Toast[] = [];
let next = 1;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

/** A short message at the bottom of the screen, read out by screen readers. */
export function toast(text: string, tone: "ok" | "error" = "ok") {
  const t = { id: next++, text, tone };
  toasts = [...toasts.slice(-2), t];
  emit();
  setTimeout(
    () => {
      toasts = toasts.filter((x) => x.id !== t.id);
      emit();
    },
    tone === "error" ? 6000 : 3500,
  );
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function Toaster() {
  const items = useSyncExternalStore(
    subscribe,
    () => toasts,
    () => toasts,
  );
  useEffect(() => () => listeners.clear(), []);
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-[calc(5.5rem+env(safe-area-inset-bottom))] z-[60] flex flex-col items-center gap-2 px-4 md:bottom-6"
    >
      {items.map((t) => (
        <div
          key={t.id}
          role={t.tone === "error" ? "alert" : "status"}
          className={cn(
            "toast pointer-events-auto flex max-w-md items-center gap-2.5 rounded-full px-4 py-2.5 text-sm font-medium shadow-lg",
            t.tone === "error" ? "bg-negative text-white" : "bg-foreground text-background",
          )}
        >
          {t.tone === "error" ? (
            <CircleAlert className="size-4 shrink-0" aria-hidden />
          ) : (
            <CheckCircle2 className="size-4 shrink-0" aria-hidden />
          )}
          {t.text}
        </div>
      ))}
    </div>
  );
}
