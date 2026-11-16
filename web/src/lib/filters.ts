/** 列表筛选条件 <-> URL query 的双向映射（纯函数，便于单测）。 */

export const RANGE_PRESETS = ["1h", "6h", "24h", "7d", "30d"] as const;
export type RangePreset = (typeof RANGE_PRESETS)[number];

export interface TraceFilters {
  project_id: string;
  range: RangePreset;
  status?: string;
  s1_label?: string;
  route?: string;
  conf_min?: number;
  conf_max?: number;
  stage?: string;
  review_status?: string;
  sort: string;
  desc: boolean;
  page: number;
  size: number;
}

export const DEFAULT_FILTERS: Omit<TraceFilters, "project_id"> = {
  range: "24h",
  sort: "severity",
  desc: true,
  page: 0,
  size: 50,
};

const OPTIONAL_STRINGS = ["status", "s1_label", "route", "stage", "review_status"] as const;

export function parseFilters(params: URLSearchParams, defaultProject: string): TraceFilters {
  const f: TraceFilters = { ...DEFAULT_FILTERS, project_id: params.get("project_id") ?? defaultProject };
  const range = params.get("range");
  if (range && (RANGE_PRESETS as readonly string[]).includes(range)) f.range = range as RangePreset;
  for (const k of OPTIONAL_STRINGS) {
    const v = params.get(k);
    if (v) f[k] = v;
  }
  const cmin = params.get("conf_min");
  const cmax = params.get("conf_max");
  if (cmin !== null && cmin !== "" && !Number.isNaN(+cmin)) f.conf_min = +cmin;
  if (cmax !== null && cmax !== "" && !Number.isNaN(+cmax)) f.conf_max = +cmax;
  if (params.get("sort")) f.sort = params.get("sort")!;
  if (params.get("desc") !== null) f.desc = params.get("desc") !== "false";
  const page = parseInt(params.get("page") ?? "", 10);
  if (!Number.isNaN(page) && page >= 0) f.page = page;
  const size = parseInt(params.get("size") ?? "", 10);
  if (!Number.isNaN(size) && size > 0 && size <= 100) f.size = size;
  return f;
}

/** 只把与默认值不同的字段写进 URL，保持地址栏干净。 */
export function serializeFilters(f: TraceFilters, defaultProject: string): URLSearchParams {
  const p = new URLSearchParams();
  if (f.project_id !== defaultProject) p.set("project_id", f.project_id);
  if (f.range !== DEFAULT_FILTERS.range) p.set("range", f.range);
  for (const k of OPTIONAL_STRINGS) {
    if (f[k]) p.set(k, f[k]!);
  }
  if (f.conf_min !== undefined) p.set("conf_min", String(f.conf_min));
  if (f.conf_max !== undefined) p.set("conf_max", String(f.conf_max));
  if (f.sort !== DEFAULT_FILTERS.sort) p.set("sort", f.sort);
  if (f.desc !== DEFAULT_FILTERS.desc) p.set("desc", String(f.desc));
  if (f.page !== 0) p.set("page", String(f.page));
  if (f.size !== DEFAULT_FILTERS.size) p.set("size", String(f.size));
  return p;
}

export function rangeToWindow(range: RangePreset, now: Date = new Date()): { from: string; to: string } {
  const ms: Record<RangePreset, number> = {
    "1h": 3_600_000,
    "6h": 6 * 3_600_000,
    "24h": 24 * 3_600_000,
    "7d": 7 * 86_400_000,
    "30d": 30 * 86_400_000,
  };
  // 对齐到分钟，避免每次轮询 query key 都变
  const to = new Date(Math.ceil(now.getTime() / 60_000) * 60_000);
  const from = new Date(to.getTime() - ms[range]);
  return { from: from.toISOString(), to: to.toISOString() };
}

/** 转成 /api/traces 的 query 参数。 */
export function toApiQuery(f: TraceFilters, now?: Date) {
  const { from, to } = rangeToWindow(f.range, now);
  return {
    project_id: f.project_id,
    from,
    to,
    status: f.status,
    s1_label: f.s1_label,
    route: f.route,
    conf_min: f.conf_min,
    conf_max: f.conf_max,
    stage: f.stage,
    review_status: f.review_status,
    sort: f.sort,
    desc: f.desc,
    page: f.page,
    size: f.size,
  };
}
