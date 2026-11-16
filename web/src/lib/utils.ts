import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return "–";
  if (ms < 1000) return `${ms.toFixed(0)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`;
  return `${(ms / 60_000).toFixed(1)} min`;
}

export function fmtUsd(v: number | string | null | undefined): string {
  if (v == null) return "–";
  const n = typeof v === "string" ? parseFloat(v) : v;
  if (Number.isNaN(n)) return "–";
  return n < 0.01 ? `$${n.toFixed(5)}` : `$${n.toFixed(3)}`;
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null) return "–";
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString();
}

export const SHOW_CONTENT = import.meta.env.VITE_SHOW_CONTENT === "true";
export const GRAFANA_URL: string = import.meta.env.VITE_GRAFANA_URL ?? "http://localhost:3000";
export const DEFAULT_PROJECT: string = import.meta.env.VITE_PROJECT_ID ?? "demo";
