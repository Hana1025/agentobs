import { describe, expect, it } from "vitest";
import { DEFAULT_FILTERS, parseFilters, rangeToWindow, serializeFilters, toApiQuery } from "./filters";

describe("filters <-> URL", () => {
  it("parses defaults from an empty query", () => {
    const f = parseFilters(new URLSearchParams(""), "demo");
    expect(f).toEqual({ ...DEFAULT_FILTERS, project_id: "demo" });
  });

  it("round-trips every field", () => {
    const p = new URLSearchParams({
      project_id: "p2",
      range: "7d",
      status: "ERROR",
      s1_label: "suspicious",
      route: "escalate",
      conf_min: "0.3",
      conf_max: "0.9",
      stage: "structure",
      review_status: "pending",
      sort: "cost",
      desc: "false",
      page: "2",
      size: "25",
    });
    const f = parseFilters(p, "demo");
    expect(f.conf_min).toBe(0.3);
    expect(f.desc).toBe(false);
    expect(f.page).toBe(2);
    const back = serializeFilters(f, "demo");
    expect(parseFilters(back, "demo")).toEqual(f);
    expect(back.get("project_id")).toBe("p2");
  });

  it("omits default values from the URL", () => {
    const f = parseFilters(new URLSearchParams(""), "demo");
    expect(serializeFilters(f, "demo").toString()).toBe("");
  });

  it("ignores invalid values", () => {
    const f = parseFilters(new URLSearchParams({ range: "99y", page: "-1", size: "1000", conf_min: "abc" }), "demo");
    expect(f.range).toBe("24h");
    expect(f.page).toBe(0);
    expect(f.size).toBe(50);
    expect(f.conf_min).toBeUndefined();
  });
});

describe("rangeToWindow", () => {
  it("aligns to the minute and spans the preset", () => {
    const now = new Date("2026-11-17T10:00:30Z");
    const w = rangeToWindow("1h", now);
    expect(w.to).toBe("2026-11-17T10:01:00.000Z");
    expect(w.from).toBe("2026-11-17T09:01:00.000Z");
  });

  it("builds api query with from/to", () => {
    const f = parseFilters(new URLSearchParams({ range: "6h" }), "demo");
    const q = toApiQuery(f, new Date("2026-11-17T10:00:00Z"));
    expect(q.project_id).toBe("demo");
    expect(Date.parse(q.to) - Date.parse(q.from)).toBe(6 * 3_600_000);
  });
});
