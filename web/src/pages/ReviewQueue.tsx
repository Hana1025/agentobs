import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useReviewQueue, useSubmitReview, type TriageDto } from "@/api/hooks";
import { ConfidenceBar, LabelBadge, RouteBadge } from "@/components/Badges";
import { DEFAULT_PROJECT, fmtTime } from "@/lib/utils";

export const LABELS = ["normal", "suspicious", "failed"] as const;
export const ROOT_CAUSES = [
  "input_extraction_error",
  "missing_or_misclassified_input",
  "output_corruption",
  "scoring_anomaly",
  "model_disagreement",
  "provider_failure",
  "timeout_cascade",
  "other",
] as const;

export default function ReviewQueue() {
  const [params, setParams] = useSearchParams();
  const project = params.get("project_id") ?? DEFAULT_PROJECT;
  const page = parseInt(params.get("page") ?? "0", 10) || 0;
  const q = useReviewQueue(project, page);
  const [open, setOpen] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const items = q.data?.items ?? [];
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">复核队列</h1>
        <input className="w-28" value={project} onChange={(e) => { const p = new URLSearchParams(params); p.set("project_id", e.target.value); setParams(p); }} />
        <span className="muted text-xs">{q.data ? `${q.data.total} 条待复核` : ""}</span>
        {toast && <span className="badge accent ml-auto" role="status">{toast}</span>}
      </div>
      {q.error && <div style={{ color: "var(--error)" }}>加载失败：{String(q.error)}</div>}
      <div className="space-y-2">
        {items.map((it) => (
          <div key={it.triage.id} className="card p-3">
            <div className="flex items-center gap-3 flex-wrap cursor-pointer" onClick={() => setOpen(open === it.triage.id ? null : it.triage.id)}>
              <span className="muted text-xs">sev {it.severity}</span>
              <Link className="font-mono underline" to={`/traces/${it.triage.traceId}`} onClick={(e) => e.stopPropagation()}>
                {it.jobId ?? it.triage.traceId.slice(0, 12)}
              </Link>
              <LabelBadge label={it.triage.s1Label} />
              <RouteBadge route={it.triage.route} />
              {it.suspectStage && <span className="text-xs">阶段 {it.suspectStage}</span>}
              {it.triage.s2RootCause && (
                <span className="text-xs">
                  S2: {it.triage.s2RootCause} <ConfidenceBar value={it.triage.s2Confidence} />
                </span>
              )}
              <span className="muted text-xs ml-auto">{fmtTime(it.triage.createdAt)}</span>
            </div>
            {it.triage.s2Summary && <div className="text-sm mt-1 muted">{it.triage.s2Summary}</div>}
            {open === it.triage.id && (
              <ReviewForm
                triage={it.triage}
                onDone={() => {
                  setOpen(null);
                  setToast("已加入评测集");
                  setTimeout(() => setToast(null), 3000);
                }}
              />
            )}
          </div>
        ))}
        {q.data && items.length === 0 && <div className="muted">队列为空</div>}
      </div>
      <div className="flex gap-2 text-sm">
        <button className="btn" disabled={page <= 0} onClick={() => { const p = new URLSearchParams(params); p.set("page", String(page - 1)); setParams(p); }}>上一页</button>
        <button className="btn" disabled={!q.data || (page + 1) * q.data.size >= q.data.total} onClick={() => { const p = new URLSearchParams(params); p.set("page", String(page + 1)); setParams(p); }}>下一页</button>
      </div>
    </div>
  );
}

export function ReviewForm({ triage, onDone }: { triage: TriageDto; onDone: () => void }) {
  const submit = useSubmitReview();
  const [label, setLabel] = useState<string>(triage.s1Label ?? "suspicious");
  const [rootCause, setRootCause] = useState<string>(triage.s2RootCause ?? "");
  const [note, setNote] = useState("");

  return (
    <form
      className="mt-3 flex flex-wrap items-end gap-3 text-sm"
      data-testid="review-form"
      onSubmit={(e) => {
        e.preventDefault();
        submit.mutate(
          { triage_id: triage.id, human_label: label, human_root_cause: rootCause || undefined, note: note || undefined },
          { onSuccess: onDone },
        );
      }}
    >
      <label className="flex flex-col text-xs">
        <span className="muted">标签</span>
        <select aria-label="human_label" value={label} onChange={(e) => setLabel(e.target.value)}>
          {LABELS.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col text-xs">
        <span className="muted">根因</span>
        <select aria-label="human_root_cause" value={rootCause} onChange={(e) => setRootCause(e.target.value)}>
          <option value="">（无）</option>
          {ROOT_CAUSES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col text-xs flex-1 min-w-48">
        <span className="muted">备注</span>
        <input aria-label="note" value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <button className="btn primary" type="submit" disabled={submit.isPending}>
        {submit.isPending ? "提交中…" : "提交复核"}
      </button>
      {submit.error && <span style={{ color: "var(--error)" }}>{String(submit.error)}</span>}
    </form>
  );
}
