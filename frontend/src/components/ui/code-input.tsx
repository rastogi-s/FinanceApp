"use client";

import { Input } from "@/components/ui/input";

/** The 6-digit code from an authenticator app. Phones offer it from the keyboard; it submits itself when full. */
export function CodeInput({
  value,
  onChange,
  onComplete,
  id,
  disabled,
  autoFocus = true,
}: {
  value: string;
  onChange: (v: string) => void;
  onComplete?: (v: string) => void;
  id?: string;
  disabled?: boolean;
  autoFocus?: boolean;
}) {
  return (
    <Input
      id={id}
      value={value}
      disabled={disabled}
      autoFocus={autoFocus}
      inputMode="numeric"
      autoComplete="one-time-code"
      pattern="[0-9]*"
      maxLength={7}
      placeholder="123 456"
      className="h-14 text-center font-mono text-2xl tracking-[0.35em] md:h-14 md:text-2xl"
      onChange={(e) => {
        const digits = e.target.value.replace(/\D/g, "").slice(0, 6);
        onChange(digits);
        if (digits.length === 6) onComplete?.(digits);
      }}
    />
  );
}
