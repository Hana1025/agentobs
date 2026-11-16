import { describe, expect, it } from "vitest";
import { layoutRows, ticks, visibleRange, type SpanLike } from "./layout";

const t = (s: number) => new Date(1_700_000_000_000 + s).toISOString();

const spans: SpanLike[] = [
  { spanId: "root", parentSpanId: null, name: "job j", startTime: t(0), endTime: t(1000), durationMs: 1000, statusCode: "OK" },
  { spanId: "stage", parentSpanId: "root", name: "stage extract", startTime: t(100), endTime: t(900), durationMs: 800, statusCode: "OK" },
  { spanId: "c2", parentSpanId: "stage", name: "chat m", startTime: t(500), endTime: t(800), durationMs: 300, statusCode: "OK", attempt: 2 },
  { spanId: "c1", parentSpanId: "stage", name: "chat m", startTime: t(150), endTime: t(300), durationMs: 150, statusCode: "ERROR", attempt: 1 },
  { spanId: "orphan", parentSpanId: "missing", name: "stage x", startTime: t(950), endTime: t(1000), durationMs: 50, statusCode: "OK" },
];

describe("layoutRows", () => {
  it("orders rows depth-first with siblings by start time and computes depth", () => {
    const rows = layoutRows(spans, new Set());
    expect(rows.map((r) => r.span.spanId)).toEqual(["root", "stage", "c1", "c2", "orphan"]);
    expect(rows.map((r) => r.depth)).toEqual([0, 1, 2, 2, 0]);
  });

  it("computes proportional left/width", () => {
    const rows = layoutRows(spans, new Set());
    const stage = rows.find((r) => r.span.spanId === "stage")!;
    expect(stage.left).toBeCloseTo(0.1);
    expect(stage.width).toBeCloseTo(0.8);
    const root = rows[0];
    expect(root.left).toBe(0);
    expect(root.width).toBe(1);
  });

  it("marks retry attempts: failed first attempt and attempt > 1", () => {
    const rows = layoutRows(spans, new Set());
    expect(rows.find((r) => r.span.spanId === "c1")!.isRetryAttempt).toBe(true);
    expect(rows.find((r) => r.span.spanId === "c1")!.isError).toBe(true);
    expect(rows.find((r) => r.span.spanId === "c2")!.isRetryAttempt).toBe(true);
    // 单次成功的尝试不算重试
    const single = layoutRows(
      [spans[0], { ...spans[2], parentSpanId: "root", spanId: "only", attempt: 1, statusCode: "OK" }],
      new Set(),
    );
    expect(single.find((r) => r.span.spanId === "only")!.isRetryAttempt).toBe(false);
  });

  it("collapses subtrees", () => {
    const rows = layoutRows(spans, new Set(["stage"]));
    expect(rows.map((r) => r.span.spanId)).toEqual(["root", "stage", "orphan"]);
    expect(rows[1].hasChildren).toBe(true);
  });

  it("handles empty input", () => {
    expect(layoutRows([], new Set())).toEqual([]);
  });
});

describe("visibleRange", () => {
  it("returns a window with buffer", () => {
    expect(visibleRange(1000, 0, 220, 22)).toEqual([0, 15]);
    expect(visibleRange(1000, 2200, 220, 22)).toEqual([95, 115]);
    expect(visibleRange(100, 2200, 220, 22)).toEqual([95, 100]);
  });
});

describe("ticks", () => {
  it("labels ms and s", () => {
    const tk = ticks(2000, 4);
    expect(tk[0]).toEqual({ pos: 0, label: "0ms" });
    expect(tk[4]).toEqual({ pos: 1, label: "2.0s" });
  });
});
