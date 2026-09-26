import type { ReactNode } from "react";

import { classes } from "../format";

export type Tone = "green" | "red" | "amber" | "indigo" | "slate";

const TONES = new Map<Tone, string>([
  ["green", "bg-emerald-50 text-emerald-700 ring-emerald-600/20"],
  ["red", "bg-rose-50 text-rose-700 ring-rose-600/20"],
  ["amber", "bg-amber-50 text-amber-800 ring-amber-600/20"],
  ["indigo", "bg-indigo-50 text-indigo-700 ring-indigo-600/20"],
  ["slate", "bg-slate-100 text-slate-700 ring-slate-500/20"],
]);

export function Badge({ tone = "slate", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={classes(
        "inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset whitespace-nowrap",
        TONES.get(tone),
      )}
    >
      {children}
    </span>
  );
}
