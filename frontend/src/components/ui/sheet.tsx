"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A bottom sheet on a phone (thumb-reachable, swipe-friendly height) and a centered dialog on a laptop.
 * Built on Radix Dialog: focus is trapped inside, Escape closes it, and screen readers announce the title.
 */
export function Sheet({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  className,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: React.ReactNode;
  children: React.ReactNode;
  /** Stays put below the scrolling body (a message box, say). */
  footer?: React.ReactNode;
  className?: string;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="sheet-overlay fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]" />
        <Dialog.Content
          className={cn(
            "sheet-content fixed inset-x-0 bottom-0 z-50 flex max-h-[92dvh] flex-col rounded-t-[1.75rem] border bg-card text-card-foreground shadow-2xl outline-none",
            "md:inset-x-auto md:top-1/2 md:bottom-auto md:left-1/2 md:w-full md:max-w-lg md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-[1.5rem]",
            className,
          )}
        >
          <div className="mx-auto mt-2.5 h-1.5 w-10 shrink-0 rounded-full bg-muted md:hidden" aria-hidden />
          <div className="flex items-start gap-3 px-5 pt-4 pb-2 md:pt-5">
            <div className="min-w-0 flex-1">
              <Dialog.Title className="text-lg font-semibold tracking-tight">{title}</Dialog.Title>
              {description ? (
                <Dialog.Description className="mt-1 text-sm text-muted-foreground">{description}</Dialog.Description>
              ) : (
                <Dialog.Description className="sr-only">{title}</Dialog.Description>
              )}
            </div>
            <Dialog.Close
              className="-mr-1 grid size-9 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label="Close"
            >
              <X className="size-5" aria-hidden />
            </Dialog.Close>
          </div>
          {footer ? (
            <>
              <div className="min-h-0 flex-1 overflow-y-auto px-5 pt-2 pb-4">{children}</div>
              <div className="shrink-0 border-t px-5 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))]">{footer}</div>
            </>
          ) : (
            <div className="overflow-y-auto px-5 pt-2 pb-[max(1.25rem,env(safe-area-inset-bottom))]">{children}</div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
