import { cn } from "@/lib/utils";

export function StatusBadge({ status }: { status?: string | null }) {
  if (!status) return <span className="muted">–</span>;
  return <span className={cn("badge", status === "ERROR" && "error")}>{status}</span>;
}

export function LabelBadge({ label }: { label?: string | null }) {
  if (!label) return <span className="muted">–</span>;
  return <span className={cn("badge", label === "failed" && "error", label === "suspicious" && "warn")}>{label}</span>;
}

export function RouteBadge({ route }: { route?: string | null }) {
  if (!route) return <span className="muted">–</span>;
  return <span className={cn("badge", route === "escalate" && "error", route === "alert" && "warn")}>{route}</span>;
}

export function ReviewBadge({ status }: { status?: string | null }) {
  if (!status || status === "none") return <span className="muted">–</span>;
  return <span className={cn("badge", status === "pending" && "accent")}>{status}</span>;
}

export function ConfidenceBar({ value }: { value?: number | null }) {
  if (value == null) return <span className="muted">–</span>;
  return (
    <span className="inline-flex items-center gap-1">
      <span className="inline-block h-2 w-16 rounded" style={{ background: "var(--border)" }}>
        <span className="block h-2 rounded" style={{ width: `${Math.round(value * 100)}%`, background: "var(--accent)" }} />
      </span>
      <span className="text-xs">{(value * 100).toFixed(0)}%</span>
    </span>
  );
}
