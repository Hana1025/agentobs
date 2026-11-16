/** 瀑布图布局计算：纯函数，不依赖 React，便于单测。 */

export interface SpanLike {
  spanId: string;
  parentSpanId?: string | null;
  name: string;
  startTime: string;
  endTime: string;
  durationMs: number;
  statusCode: string;
  attempt?: number | null;
  depth?: number;
}

export interface Row {
  span: SpanLike;
  depth: number;
  /** 0..1 相对整条 trace 的起点 */
  left: number;
  /** 0..1 相对整条 trace 的总时长，最小宽度由渲染层处理 */
  width: number;
  hasChildren: boolean;
  isRetryAttempt: boolean;
  isError: boolean;
}

export const ROW_HEIGHT = 22;
export const INDENT = 14;

/**
 * 输入：任意顺序的 span 列表 + 折叠集合。
 * 输出：可见行（DFS 顺序，兄弟按 start_time），每行带 left/width 比例。
 */
export function layoutRows(spans: SpanLike[], collapsed: Set<string>): Row[] {
  if (spans.length === 0) return [];
  const ids = new Set(spans.map((s) => s.spanId));
  const children = new Map<string, SpanLike[]>();
  const roots: SpanLike[] = [];
  for (const s of spans) {
    if (s.parentSpanId && ids.has(s.parentSpanId)) {
      const arr = children.get(s.parentSpanId) ?? [];
      arr.push(s);
      children.set(s.parentSpanId, arr);
    } else {
      roots.push(s);
    }
  }
  const byStart = (a: SpanLike, b: SpanLike) => Date.parse(a.startTime) - Date.parse(b.startTime) || a.spanId.localeCompare(b.spanId);
  roots.sort(byStart);
  for (const arr of children.values()) arr.sort(byStart);

  const t0 = Math.min(...spans.map((s) => Date.parse(s.startTime)));
  const t1 = Math.max(...spans.map((s) => Date.parse(s.endTime)));
  const total = Math.max(1, t1 - t0);

  // 同名同父的 chat span 且 attempt > 1 视为重试尝试；attempt == 1 但之后还有尝试的也算失败尝试
  const attemptGroups = new Map<string, SpanLike[]>();
  for (const s of spans) {
    if (s.attempt != null) {
      const key = `${s.parentSpanId ?? ""}|${s.name}`;
      const arr = attemptGroups.get(key) ?? [];
      arr.push(s);
      attemptGroups.set(key, arr);
    }
  }

  const rows: Row[] = [];
  const visit = (s: SpanLike, depth: number) => {
    const kids = children.get(s.spanId) ?? [];
    const group = s.attempt != null ? attemptGroups.get(`${s.parentSpanId ?? ""}|${s.name}`) ?? [] : [];
    const isRetryAttempt = group.length > 1 && (s.attempt! > 1 || s.statusCode === "ERROR");
    rows.push({
      span: s,
      depth,
      left: (Date.parse(s.startTime) - t0) / total,
      width: Math.max(0, (Date.parse(s.endTime) - Date.parse(s.startTime)) / total),
      hasChildren: kids.length > 0,
      isRetryAttempt,
      isError: s.statusCode === "ERROR",
    });
    if (!collapsed.has(s.spanId)) {
      for (const k of kids) visit(k, depth + 1);
    }
  };
  for (const r of roots) visit(r, 0);
  return rows;
}

/** 窗口化：只渲染 scrollTop..scrollTop+viewportHeight 范围内的行（加上下各 5 行缓冲）。 */
export function visibleRange(rowCount: number, scrollTop: number, viewportHeight: number, rowHeight = ROW_HEIGHT): [number, number] {
  const buffer = 5;
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - buffer);
  const end = Math.min(rowCount, Math.ceil((scrollTop + viewportHeight) / rowHeight) + buffer);
  return [start, end];
}

/** 时间轴刻度：返回 0..1 的位置和标签。 */
export function ticks(totalMs: number, count = 5): { pos: number; label: string }[] {
  const out = [];
  for (let i = 0; i <= count; i++) {
    const ms = (totalMs * i) / count;
    out.push({ pos: i / count, label: ms < 1000 ? `${ms.toFixed(0)}ms` : `${(ms / 1000).toFixed(1)}s` });
  }
  return out;
}
