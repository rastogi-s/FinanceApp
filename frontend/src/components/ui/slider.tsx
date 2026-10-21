import { useId } from "react";

/**
 * A labelled range slider: a native <input type="range">, so arrow keys, screen readers and touch all work as
 * the platform does, styled to match (globals.css, .slider). The value is shown beside the label as text.
 */
export function Slider({
  label,
  value,
  display,
  min,
  max,
  step,
  onChange,
  hint,
}: {
  label: string;
  value: number;
  display: React.ReactNode;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  hint?: React.ReactNode;
}) {
  const id = useId();
  const fill = ((Math.min(Math.max(value, min), max) - min) / (max - min || 1)) * 100;
  return (
    <div className="grid grid-cols-1 gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        <span className="text-sm font-semibold tabular-nums">{display}</span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-describedby={hint ? `${id}-hint` : undefined}
        className="slider"
        style={{ "--fill": `${fill}%` } as React.CSSProperties}
      />
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  );
}
