import { useEffect, useMemo, useRef, useState } from "react";
import { INDENT, ROW_HEIGHT, layoutRows, ticks, visibleRange, type SpanLike } from "./layout";
import { fmtMs } from "@/lib/utils";

interface Props {
  spans: SpanLike[];
  selectedId?: string | null;
  highlightId?: string | null;
  onSelect?: (spanId: string) => void;
}

const LABEL_W = 320;
const VIRTUALIZE_ABOVE = 500;

/** 自己用 SVG 画的瀑布图：每行一个 span，按父子缩进，横向按时间比例；可折叠子树；超过 500 行只渲染可见行。 */
export default function Waterfall({ spans, selectedId, highlightId, onSelect }: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [width, setWidth] = useState(900);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportH, setViewportH] = useState(600);
  const containerRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => layoutRows(spans, collapsed), [spans, collapsed]);
  const totalMs = useMemo(() => {
    if (spans.length === 0) return 0;
    const t0 = Math.min(...spans.map((s) => Date.parse(s.startTime)));
    const t1 = Math.max(...spans.map((s) => Date.parse(s.endTime)));
    return t1 - t0;
  }, [spans]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setWidth(el.clientWidth);
      setViewportH(el.clientHeight);
    });
    ro.observe(el);
    setWidth(el.clientWidth);
    setViewportH(el.clientHeight);
    return () => ro.disconnect();
  }, []);

  // 高亮目标：滚动到对应行
  useEffect(() => {
    if (!highlightId || !containerRef.current) return;
    const idx = rows.findIndex((r) => r.span.spanId === highlightId);
    if (idx < 0) return;
    const top = idx * ROW_HEIGHT;
    const el = containerRef.current;
    if (top < el.scrollTop || top + ROW_HEIGHT > el.scrollTop + el.clientHeight) {
      el.scrollTo({ top: Math.max(0, top - el.clientHeight / 2), behavior: "smooth" });
    }
  }, [highlightId, rows]);

  const barX0 = LABEL_W;
  const barW = Math.max(100, width - LABEL_W - 16);
  const virtual = rows.length > VIRTUALIZE_ABOVE;
  const [start, end] = virtual ? visibleRange(rows.length, scrollTop, viewportH) : [0, rows.length];
  const height = rows.length * ROW_HEIGHT + 24;

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div
      ref={containerRef}
      className="card overflow-auto"
      style={{ maxHeight: "70vh", minHeight: 200 }}
      onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
      data-testid="waterfall"
    >
      <svg width={width} height={height} style={{ display: "block", fontSize: 12 }}>
        {/* 时间轴 */}
        <g>
          {ticks(totalMs).map((t) => (
            <g key={t.pos}>
              <line x1={barX0 + t.pos * barW} x2={barX0 + t.pos * barW} y1={0} y2={height} stroke="var(--border)" strokeDasharray="2 3" />
              <text x={barX0 + t.pos * barW + 2} y={12} fill="var(--muted)" fontSize={10}>
                {t.label}
              </text>
            </g>
          ))}
        </g>
        {rows.slice(start, end).map((r, i) => {
          const idx = start + i;
          const y = 24 + idx * ROW_HEIGHT;
          const selected = r.span.spanId === selectedId;
          const highlighted = r.span.spanId === highlightId;
          const color = r.isError ? "var(--error)" : "var(--ok)";
          const x = barX0 + r.left * barW;
          const w = Math.max(2, r.width * barW);
          return (
            <g
              key={r.span.spanId}
              data-span-id={r.span.spanId}
              data-depth={r.depth}
              onClick={() => onSelect?.(r.span.spanId)}
              style={{ cursor: "pointer" }}
            >
              <rect x={0} y={y} width={width} height={ROW_HEIGHT} fill={selected ? "var(--accent)" : highlighted ? "var(--warn)" : "transparent"} opacity={selected || highlighted ? 0.15 : 1} />
              {r.hasChildren && (
                <text
                  x={4 + r.depth * INDENT}
                  y={y + 15}
                  fill="var(--muted)"
                  onClick={(e) => {
                    e.stopPropagation();
                    toggle(r.span.spanId);
                  }}
                >
                  {collapsed.has(r.span.spanId) ? "▸" : "▾"}
                </text>
              )}
              <text x={18 + r.depth * INDENT} y={y + 15} fill={r.isError ? "var(--error)" : "var(--fg)"}>
                {truncate(r.span.name, Math.max(8, Math.floor((LABEL_W - 60 - r.depth * INDENT) / 7)))}
                {r.span.attempt != null && <tspan fill="var(--muted)"> #{r.span.attempt}</tspan>}
              </text>
              <rect
                x={x}
                y={y + 5}
                width={w}
                height={ROW_HEIGHT - 10}
                rx={2}
                fill={color}
                fillOpacity={r.isRetryAttempt ? 0.25 : 0.7}
                stroke={color}
                strokeWidth={r.isRetryAttempt ? 1.5 : 0}
                strokeDasharray={r.isRetryAttempt ? "4 2" : undefined}
              />
              <text x={Math.min(x + w + 4, barX0 + barW - 50)} y={y + 15} fill="var(--muted)" fontSize={10}>
                {fmtMs(r.span.durationMs)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function truncate(s: string, n: number) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
