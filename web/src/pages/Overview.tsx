import { useSearchParams, Link } from "react-router-dom";
import { useMetricsSummary } from "@/api/hooks";
import StatCard from "@/components/StatCard";
import { RANGE_PRESETS, type RangePreset } from "@/lib/filters";
import { DEFAULT_PROJECT, fmtMs, fmtPct, fmtUsd, GRAFANA_URL } from "@/lib/utils";

export default function Overview() {
  const [params, setParams] = useSearchParams();
  const project = params.get("project_id") ?? DEFAULT_PROJECT;
  const rawRange = params.get("range") ?? "";
  const range: RangePreset = (RANGE_PRESETS as readonly string[]).includes(rawRange) ? (rawRange as RangePreset) : "24h";
  const q = useMetricsSummary(project, range);
  const m = q.data;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">总览</h1>
        <select
          value={range}
          onChange={(e) => {
            const p = new URLSearchParams(params);
            p.set("range", e.target.value);
            setParams(p);
          }}
        >
          {RANGE_PRESETS.map((r) => (
            <option key={r} value={r}>
              最近 {r}
            </option>
          ))}
        </select>
        <input
          value={project}
          onChange={(e) => {
            const p = new URLSearchParams(params);
            p.set("project_id", e.target.value);
            setParams(p);
          }}
          placeholder="project_id"
          className="w-32"
        />
        <span className="muted text-xs">{q.isFetching ? "刷新中…" : q.error ? `加载失败：${String(q.error)}` : "每 30 秒自动刷新"}</span>
        <a className="ml-auto text-sm underline" href={`${GRAFANA_URL}/d/agentobs-llm`} target="_blank" rel="noreferrer">
          Grafana：LLM 调用 ↗
        </a>
        <a className="text-sm underline" href={`${GRAFANA_URL}/d/agentobs-triage`} target="_blank" rel="noreferrer">
          Grafana：分诊 ↗
        </a>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCard label="成功率" value={fmtPct(m?.successRate)} hint={m ? `${m.succeeded}/${m.jobs} jobs` : undefined} />
        <StatCard label="P95 job 耗时" value={fmtMs(m?.p95DurationMs)} />
        <StatCard label="平均单 job 成本" value={fmtUsd(m?.avgCostUsd)} />
        <StatCard label="升级率" value={fmtPct(m?.escalationRate)} hint="route = escalate / 已分诊" />
        <StatCard label="待复核" value={m ? String(m.pendingReviews) : "–"} />
      </div>
      <div className="muted text-sm">
        查看 <Link className="underline" to={`/traces?project_id=${project}&range=${range}`}>trace 列表</Link>，按 severity 降序。
      </div>
    </div>
  );
}
