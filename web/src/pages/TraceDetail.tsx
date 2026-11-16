import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTrace, type SpanDto, type TriageDto } from "@/api/hooks";
import { ConfidenceBar, LabelBadge, ReviewBadge, RouteBadge, StatusBadge } from "@/components/Badges";
import JsonTree from "@/components/JsonTree";
import Waterfall from "@/components/waterfall/Waterfall";
import { fmtMs, fmtTime, fmtUsd } from "@/lib/utils";

export default function TraceDetail() {
  const { traceId } = useParams();
  const q = useTrace(traceId);
  const [selected, setSelected] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const spans = q.data?.spans ?? [];
  const selectedSpan = useMemo(() => spans.find((s) => s.spanId === selected), [spans, selected]);

  if (q.isLoading) return <div className="muted">加载中…</div>;
  if (q.error || !q.data) return <div style={{ color: "var(--error)" }}>加载失败：{String(q.error)}</div>;
  const { summary, triage, reviews } = q.data;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <Link to="/traces" className="muted text-sm">← 列表</Link>
        <h1 className="font-semibold font-mono">{summary.jobId ?? summary.traceId}</h1>
        <StatusBadge status={summary.rootStatus} />
        <span className="muted text-xs">trace {summary.traceId}</span>
        <span className="text-sm">{fmtTime(summary.startTime)}</span>
        <span className="text-sm">{fmtMs(Date.parse(summary.endTime) - Date.parse(summary.startTime))}</span>
        <span className="text-sm">{fmtUsd(summary.costUsd)}</span>
        <span className="text-sm muted">{summary.spanCount} spans · {summary.errorCount} errors · tokens {summary.inputTokens ?? 0}/{summary.outputTokens ?? 0}</span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-3">
        <div className="space-y-3 min-w-0">
          <Waterfall spans={spans} selectedId={selected} highlightId={highlight} onSelect={(id) => { setSelected(id); setHighlight(null); }} />
          <div className="card p-3">
            <div className="font-semibold text-sm mb-2">{selectedSpan ? selectedSpan.name : "点击一行查看属性"}</div>
            {selectedSpan && <SpanPanel span={selectedSpan} />}
          </div>
        </div>
        <div className="space-y-3 min-w-0">
          <TriagePanel triage={triage} onEvidenceClick={(id) => { setHighlight(id); setSelected(id); }} spanIds={new Set(spans.map((s) => s.spanId))} />
          <div className="card p-3">
            <div className="font-semibold text-sm mb-2">复核记录</div>
            {reviews.length === 0 && <div className="muted text-sm">无</div>}
            {reviews.map((r) => (
              <div key={r.id} className="text-sm border-t pt-2 mt-2" style={{ borderColor: "var(--border)" }}>
                <div><span className="muted">{r.reviewer}</span> · {fmtTime(r.createdAt)}</div>
                <div><LabelBadge label={r.humanLabel} /> {r.humanRootCause && <span className="ml-2 text-xs">{r.humanRootCause}</span>}</div>
                {r.note && <div className="muted text-xs mt-1">{r.note}</div>}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function SpanPanel({ span }: { span: SpanDto }) {
  return (
    <div className="space-y-2 text-sm">
      <div className="flex gap-3 flex-wrap text-xs">
        <span>span {span.spanId}</span>
        <StatusBadge status={span.statusCode} />
        <span>{fmtMs(span.durationMs)}</span>
        {span.stage && <span>stage {span.stage}</span>}
        {span.model && <span>model {span.model}</span>}
        {span.attempt != null && <span>attempt {span.attempt}</span>}
        {span.costUsd != null && <span>{fmtUsd(span.costUsd)}</span>}
      </div>
      {span.statusMessage && <div style={{ color: "var(--error)" }} className="text-xs">{span.statusMessage}</div>}
      <JsonTree value={span.attributes} />
      {span.events && span.events.length > 0 && (
        <div>
          <div className="muted text-xs mt-2">events</div>
          <JsonTree value={{ events: span.events }} />
        </div>
      )}
    </div>
  );
}

interface Answer {
  answer?: string;
  probs?: Record<string, number>;
  confidence?: number;
}

export function TriagePanel({ triage, onEvidenceClick, spanIds }: { triage?: TriageDto | null; onEvidenceClick: (spanId: string) => void; spanIds: Set<string> }) {
  if (!triage) {
    return (
      <div className="card p-3">
        <div className="font-semibold text-sm">分诊</div>
        <div className="muted text-sm">尚无分诊结果</div>
      </div>
    );
  }
  const answers = (triage.s1Answers ?? {}) as Record<string, Answer>;
  return (
    <div className="card p-3 space-y-3 text-sm">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-semibold">分诊</span>
        <RouteBadge route={triage.route} />
        <LabelBadge label={triage.s1Label} />
        <ReviewBadge status={triage.reviewStatus} />
        <span className="muted text-xs ml-auto">{triage.s1Backend} · {triage.triageVersion.slice(0, 12)} · {fmtUsd(triage.triageCostUsd)}</span>
      </div>

      <div>
        <div className="muted text-xs">规则命中</div>
        <div>{triage.rulesFired.length ? triage.rulesFired.join(", ") : <span className="muted">无</span>}</div>
      </div>

      <div>
        <div className="muted text-xs">System 1</div>
        {Object.entries(answers).map(([qid, a]) => (
          <div key={qid} className="mt-1">
            <div className="flex items-center gap-2">
              <span className="w-28 text-xs">{qid}</span>
              <span className="font-medium">{a.answer}</span>
              <ConfidenceBar value={a.confidence} />
            </div>
            {a.probs && (
              <div className="flex gap-1 mt-1 ml-28">
                {Object.entries(a.probs).map(([opt, p]) => (
                  <span key={opt} className="text-xs muted" title={opt}>
                    <span className="inline-block h-1.5 rounded align-middle mr-0.5" style={{ width: Math.max(2, p * 60), background: opt === a.answer ? "var(--accent)" : "var(--border)" }} />
                    {opt} {(p * 100).toFixed(0)}%
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      <div>
        <div className="muted text-xs">System 2</div>
        {triage.s2RootCause ? (
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="font-medium">{triage.s2RootCause}</span>
              <ConfidenceBar value={triage.s2Confidence} />
              <span className="muted text-xs">{triage.s2Model}</span>
            </div>
            {triage.s2Summary && <div>{triage.s2Summary}</div>}
            <ul className="list-disc ml-5 text-xs">
              {(triage.s2Evidence ?? []).map((e, i) => {
                const sid = String(e.span_id ?? "");
                const exists = spanIds.has(sid);
                return (
                  <li key={i}>
                    <button className={exists ? "underline" : "muted"} disabled={!exists} onClick={() => onEvidenceClick(sid)}>
                      {sid.slice(0, 8)}
                    </button>{" "}
                    <span className="font-mono">{String(e.attribute ?? "")}</span> — {String(e.note ?? "")}
                  </li>
                );
              })}
            </ul>
          </div>
        ) : (
          <div className="muted">{triage.route === "escalate" ? "等待 System 2" : "未升级"}</div>
        )}
      </div>
    </div>
  );
}
