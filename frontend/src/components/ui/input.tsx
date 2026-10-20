import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export function Input({ className, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      className={cn(
        "h-12 w-full rounded-xl border bg-background px-3.5 text-base outline-none transition-colors placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/20 disabled:opacity-60 aria-invalid:border-negative md:h-11 md:text-sm",
        className,
      )}
      {...props}
    />
  );
}

export function Select({ className, ...props }: React.ComponentProps<"select">) {
  return (
    <div className="relative">
      <select
        className={cn(
          "h-12 w-full appearance-none rounded-xl border bg-background px-3.5 pr-10 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/20 md:h-11 md:text-sm",
          className,
        )}
        {...props}
      />
      <ChevronDown className="pointer-events-none absolute top-1/2 right-3.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
    </div>
  );
}

/** A labelled form field with an optional hint and error, wired up for screen readers. */
export function Field({
  label,
  hint,
  error,
  children,
  id,
}: {
  label: string;
  hint?: React.ReactNode;
  error?: string | null;
  children: React.ReactElement<{ id?: string; "aria-describedby"?: string; "aria-invalid"?: boolean }>;
  id: string;
}) {
  const describedBy = [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="grid grid-cols-1 gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {React.cloneElement(children, { id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined })}
      {hint && !error && (
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="text-xs font-medium text-negative" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
