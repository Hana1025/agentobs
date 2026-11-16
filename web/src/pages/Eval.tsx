import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import { useEvalRun, useEvalRuns } from "@/api/hooks";
import { fmtTime, fmtUsd } from "@/lib/utils";

interface Metrics {
  fault_recall?: number;
  normal_false_positive_rate?: number;
  ece?: number;
  cost_per_case_usd?: number;
  latency_p50_s?: number;
  latency_p95_s?: number;
  s2_root_cause_accuracy?: number;
  reliability?: { bin_lower: number; bin_upper: number; confidence: number; accuracy: number; count: number }[];
  coverage_accuracy?: { coverage: number; accuracy: number; threshold: number }[];
  recall_by_fault_type?: Record<string, number>;
  health_precision?: Record<string, number>;
  health_recall?: Record<string, number>;
}

const pct = (v?: number) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);

export default function EvalPage() {
  const runs = useEvalRuns();
  const [selected, setSelected] = useState<number | undefined>();
  const run = useEvalRun(selected);
  const m = (run.data?.metrics ?? {}) as Metrics;

  useEffect(() => {
    if (!selected && runs.data && runs.data.length > 0) setSelected(runs.data[0].id);
  }, [runs.data, selected]);

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold">评测</h1>
      <div className="card overflow-auto">
        <table className="data w-full text-sm">
          <thead>
            <tr>
              <th>时间</th>
              <th>backend</th>
              <th>版本</th>
              <th>样本</th>
              <th>故障召回</th>
              <th>正常误报</th>
              <th>ECE</th>
              <th>S2 根因准确率</th>
              <th>单条成本</th>
              <th>p95 延迟</th>
            </tr>
          </thead>
          <tbody>
            {runs.data?.map((r) => {
              const mm = r.metrics as Metrics;
              return (
                <tr key={r.id} onClick={() => setSelected(r.id)} className="cursor-pointer" style={{ background: r.id === selected ? "color-mix(in srgb, var(--accent) 12%, transparent)" : undefined }}>
                  <td>{fmtTime(r.createdAt)}</td>
                  <td>{r.backend}</td>
                  <td className="font-mono text-xs">{r.triageVersion.slice(0, 12)}</td>
                  <td>{r.datasetSize}</td>
                  <td>{pct(mm.fault_recall)}</td>
                  <td>{pct(mm.normal_false_positive_rate)}</td>
                  <td>{mm.ece?.toFixed(3) ?? "–"}</td>
                  <td>{pct(mm.s2_root_cause_accuracy)}</td>
                  <td>{fmtUsd(mm.cost_per_case_usd)}</td>
                  <td>{mm.latency_p95_s?.toFixed(2) ?? "–"} s</td>
                </tr>
              );
            })}
            {runs.data && runs.data.length === 0 && (
              <tr><td colSpan={10} className="muted text-center py-6">还没有评测结果，运行 make eval</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {run.data && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <Chart title="可靠性曲线" option={reliabilityOption(m)} />
          <Chart title="覆盖率 — 准确率" option={coverageOption(m)} />
          <div className="card p-3 text-sm lg:col-span-2">
            <div className="font-semibold mb-2">按故障类型的召回</div>
            <div className="flex flex-wrap gap-3">
              {Object.entries(m.recall_by_fault_type ?? {}).map(([k, v]) => (
                <span key={k} className="badge">{k}: {pct(v)}</span>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function reliabilityOption(m: Metrics): echarts.EChartsOption {
  const bins = m.reliability ?? [];
  return {
    tooltip: { trigger: "axis" },
    xAxis: { type: "value", min: 0, max: 1, name: "置信度" },
    yAxis: { type: "value", min: 0, max: 1, name: "准确率" },
    series: [
      { type: "line", name: "理想", data: [[0, 0], [1, 1]], lineStyle: { type: "dashed", color: "#9ca3af" }, symbol: "none" },
      { type: "line", name: "模型", data: bins.filter((b) => b.count > 0).map((b) => [b.confidence, b.accuracy]), symbolSize: 6 },
    ],
  };
}

function coverageOption(m: Metrics): echarts.EChartsOption {
  const pts = m.coverage_accuracy ?? [];
  return {
    tooltip: { trigger: "axis", formatter: (p) => { const d = (Array.isArray(p) ? p[0] : p) as { data: number[] }; return `覆盖率 ${(d.data[0] * 100).toFixed(0)}% → 准确率 ${(d.data[1] * 100).toFixed(1)}%`; } },
    xAxis: { type: "value", min: 0, max: 1, name: "覆盖率" },
    yAxis: { type: "value", min: 0, max: 1, name: "准确率" },
    series: [{ type: "line", data: pts.map((p) => [p.coverage, p.accuracy]), symbolSize: 4 }],
  };
}

function Chart({ title, option }: { title: string; option: echarts.EChartsOption }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [option]);
  return (
    <div className="card p-3">
      <div className="font-semibold text-sm mb-2">{title}</div>
      <div ref={ref} style={{ height: 280 }} />
    </div>
  );
}
