"use client";

import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { refreshAll } from "@/lib/household";
import { cn } from "@/lib/utils";

const TRIGGER = 72; // px of pull, after resistance, that refreshes

/**
 * A home-screen app on iPhone has no browser pull-to-refresh, so this gives it one: pull down from the top of any
 * page to sync and show fresh numbers.
 */
export function PullToRefresh() {
  const [pull, setPull] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const start = useRef<number | null>(null);
  const distance = useRef(0);
  const busy = useRef(false); // ignore pulls while a sync runs

  useEffect(() => {
    const down = (e: TouchEvent) => {
      const touch = e.touches[0];
      start.current =
        window.scrollY <= 0 && e.touches.length === 1 && touch
          ? touch.clientY
          : null;
      distance.current = 0;
    };
    const move = (e: TouchEvent) => {
      const touch = e.touches[0];
      if (start.current === null || !touch || busy.current) return;
      const dy = touch.clientY - start.current;
      if (dy <= 0 || window.scrollY > 0) {
        distance.current = 0;
        setPull(0);
        return;
      }
      distance.current = Math.min(dy * 0.5, TRIGGER * 1.5);
      setPull(distance.current);
    };
    const up = () => {
      if (start.current === null) return;
      start.current = null;
      if (distance.current >= TRIGGER) {
        busy.current = true;
        setSyncing(true);
        refreshAll().finally(() => {
          busy.current = false;
          setSyncing(false);
          setPull(0);
        });
      } else {
        setPull(0);
      }
      distance.current = 0;
    };

    window.addEventListener("touchstart", down, { passive: true });
    window.addEventListener("touchmove", move, { passive: true });
    window.addEventListener("touchend", up);
    window.addEventListener("touchcancel", up);
    return () => {
      window.removeEventListener("touchstart", down);
      window.removeEventListener("touchmove", move);
      window.removeEventListener("touchend", up);
      window.removeEventListener("touchcancel", up);
    };
  }, []);

  const shown = syncing ? TRIGGER : pull;
  if (!shown) return null;
  return (
    <div
      className="pointer-events-none fixed inset-x-0 z-50 flex justify-center"
      style={{ top: `calc(env(safe-area-inset-top) + ${shown - 24}px)` }}
      role="status"
      aria-label={syncing ? "Syncing" : undefined}
    >
      <span className="grid size-9 place-items-center rounded-full border bg-background shadow-sm">
        <RefreshCw
          className={cn(
            "size-4 text-muted-foreground",
            syncing && "animate-spin motion-reduce:animate-none",
          )}
          style={
            syncing
              ? undefined
              : {
                  transform: `rotate(${(pull / TRIGGER) * 270}deg)`,
                  opacity: Math.min(1, pull / TRIGGER),
                }
          }
          aria-hidden
        />
      </span>
    </div>
  );
}
