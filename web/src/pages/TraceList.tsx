import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useTraces } from "@/api/hooks";
import { ConfidenceBar, LabelBadge, ReviewBadge, RouteBadge, StatusBadge } from "@/components/Badges";
import { parseFilters, RANGE_PRESETS, serializeFilters, type TraceFilters } from "@/lib/filters";
import { DEFAULT_PROJECT, fmtMs, fmtTime, fmtUsd } from "@/lib/utils";

const STAGES = ["extract", "structure", "score", "infrastructure", "none"];
const SORTS: [string, string][] = [
  ["severity", "severity"],
  ["start_time", "开始时间"],
  ["duration", "耗时"],
  ["cost", "成本"],
  ["s1_confidence", "S1 置信度"],
  ["s2_confidence", "S2 置信度"],
];

export default function TraceList() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(params, DEFAULT_PROJECT), [params]);
  const q = useTraces(filters);

  const update = (patch: Partial<TraceFilters>) => {
    const next = { ...filters, ...patch };
    if (!("page" in patch)) next.page = 0;
    setParams(serializeFilters(next, DEFAULT_PROJECT), { replace: true });
  };

  const sel = (key: keyof TraceFilters, options: string[], label: string) => (
    <label className="flex items-center gap-1 text-xs">
      <span className="muted">{label}</span>
      <select value={(filters[key] as string | undefined) ?? ""} onChange={(e) => update({ [key]: e.target.value || undefined } as Partial<TraceFilters>)}>
        <option value="">全部</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );

  const data = q.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 card p-3" data-testid="filters">
        <label className="flex items-center gap-1 text-xs">
          <span className="muted">项目</span>
          <input className="w-28" value={filters.project_id} onChange={(e) => update({ project_id: e.target.value })} />
        </label>
        <label className="flex items-center gap-1 text-xs">
          <span className="muted">时间</span>
          <select value={filters.range} onChange={(e) => update({ range: e.target.value as TraceFilters["range"] })}>
            {RANGE_PRESETS.map((r) => (
              <option key={r} value={r}>
                最近 {r}
              </option>
            ))}
          </select>
        </label>
        {sel("status", ["OK", "ERROR"], "状态")}
        {sel("s1_label", ["normal", "suspicious", "failed"], "S1 标签")}
        {sel("route", ["pass", "alert", "escalate"], "路由")}
        <label className="flex items-center gap-1 text-xs">
          <span className="muted">置信度</span>
          <input type="number" step="0.05" min="0" max="1" className="w-16" value={filters.conf_min ?? ""} placeholder="min"
            onChange={(e) => update({ conf_min: e.target.value === "" ? undefined : +e.target.value })} />
          <span>–</span>
          <input type="number" step="0.05" min="0" max="1" className="w-16" value={filters.conf_max ?? ""} placeholder="max"
            onChange={(e) => update({ conf_max: e.target.value === "" ? undefined : +e.target.value })} />
        </label>
        {sel("stage", STAGES, "可疑阶段")}
        {sel("review_status", ["none", "pending", "reviewed"], "复核")}
        <label className="flex items-center gap-1 text-xs">
          <span className="muted">排序</span>
          <select value={filters.sort} onChange={(e) => update({ sort: e.target.value })}>
            {SORTS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <button className="btn" onClick={() => update({ desc: !filters.desc })}>
            {filters.desc ? "↓" : "↑"}
          </button>
        </label>
        <span className="muted text-xs ml-auto">
          {q.isFetching ? "刷新中…" : data ? `${data.total} 条` : ""}
        </span>
      </div>

      {q.error && <div className="card p-3" style={{ color: "var(--error)" }}>加载失败：{String(q.error)}</div>}

      <div className="card overflow-auto">
        <table className="data w-full text-sm">
          <thead>
            <tr>
              <th>开始</th>
              <th>job</th>
              <th>状态</th>
              <th>耗时</th>
              <th>成本</th>
              <th>重试</th>
              <th>规则</th>
              <th>S1</th>
              <th>置信度</th>
              <th>路由</th>
              <th>可疑阶段</th>
              <th>sev</th>
              <th>S2 根因</th>
              <th>复核</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((t) => (
              <tr key={t.traceId}>
                <td>
                  <Link className="underline" to={`/traces/${t.traceId}`}>
                    {fmtTime(t.startTime)}
                  </Link>
                </td>
                <td className="font-mono text-xs">{t.jobId ?? t.traceId.slice(0, 8)}</td>
                <td><StatusBadge status={t.rootStatus} /></td>
                <td>{fmtMs(t.durationMs)}</td>
                <td>{fmtUsd(t.costUsd)}</td>
                <td>{t.maxAttempt && t.maxAttempt > 1 ? `×${t.maxAttempt}` : ""}</td>
                <td className="text-xs">{t.rulesFired?.join(", ")}</td>
                <td><LabelBadge label={t.s1Label} /></td>
                <td><ConfidenceBar value={t.s1Confidence} /></td>
                <td><RouteBadge route={t.route} /></td>
                <td>{t.suspectStage ?? ""}</td>
                <td>{t.severity}</td>
                <td className="text-xs">{t.s2RootCause ?? ""}</td>
                <td><ReviewBadge status={t.reviewStatus} /></td>
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr>
                <td colSpan={14} className="muted text-center py-6">没有符合条件的 trace</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center gap-2 text-sm">
        <button className="btn" disabled={filters.page <= 0} onClick={() => update({ page: filters.page - 1 })}>上一页</button>
        <span className="muted">
          {filters.page + 1} / {pages}
        </span>
        <button className="btn" disabled={filters.page + 1 >= pages} onClick={() => update({ page: filters.page + 1 })}>下一页</button>
      </div>
    </div>
  );
}
