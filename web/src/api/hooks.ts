import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, unwrap } from "./client";
import type { components } from "./schema";
import type { TraceFilters } from "@/lib/filters";
import { rangeToWindow, toApiQuery } from "@/lib/filters";

export type TraceListItem = components["schemas"]["TraceListItem"];
export type TraceDetail = components["schemas"]["TraceDetail"];
export type SpanDto = components["schemas"]["SpanDto"];
export type TriageDto = components["schemas"]["TriageDto"];
export type MetricsSummary = components["schemas"]["MetricsSummary"];
export type ReviewRequest = components["schemas"]["ReviewRequest"];
export type EvalRun = components["schemas"]["EvalRunDto"];

const POLL_MS = 30_000;

/** 窗口失焦时停止轮询。 */
export function useWindowFocused(): boolean {
  const [focused, setFocused] = useState(() => (typeof document === "undefined" ? true : document.hasFocus()));
  useEffect(() => {
    const on = () => setFocused(true);
    const off = () => setFocused(false);
    window.addEventListener("focus", on);
    window.addEventListener("blur", off);
    return () => {
      window.removeEventListener("focus", on);
      window.removeEventListener("blur", off);
    };
  }, []);
  return focused;
}

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => unwrap(await api.GET("/api/me")),
    staleTime: Infinity,
  });
}

export function useMetricsSummary(projectId: string, range: TraceFilters["range"]) {
  const focused = useWindowFocused();
  const { from, to } = rangeToWindow(range);
  return useQuery({
    queryKey: ["metrics", projectId, from, to],
    queryFn: async () =>
      unwrap(await api.GET("/api/metrics/summary", { params: { query: { project_id: projectId, from, to } } })),
    refetchInterval: focused ? POLL_MS : false,
  });
}

export function useTraces(filters: TraceFilters) {
  const focused = useWindowFocused();
  const query = toApiQuery(filters);
  return useQuery({
    queryKey: ["traces", query],
    queryFn: async () => unwrap(await api.GET("/api/traces", { params: { query } })),
    refetchInterval: focused ? POLL_MS : false,
    placeholderData: (prev) => prev,
  });
}

export function useTrace(traceId: string | undefined) {
  return useQuery({
    queryKey: ["trace", traceId],
    queryFn: async () => unwrap(await api.GET("/api/traces/{traceId}", { params: { path: { traceId: traceId! } } })),
    enabled: !!traceId,
    // 详情页不轮询
    refetchInterval: false,
  });
}

export function useReviewQueue(projectId: string, page: number) {
  const focused = useWindowFocused();
  return useQuery({
    queryKey: ["reviewQueue", projectId, page],
    queryFn: async () =>
      unwrap(await api.GET("/api/reviews/queue", { params: { query: { project_id: projectId, page } } })),
    refetchInterval: focused ? POLL_MS : false,
    placeholderData: (prev) => prev,
  });
}

export function useSubmitReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: ReviewRequest) => unwrap(await api.POST("/api/reviews", { body })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reviewQueue"] });
      qc.invalidateQueries({ queryKey: ["traces"] });
      qc.invalidateQueries({ queryKey: ["metrics"] });
    },
  });
}

export function useEvalRuns() {
  return useQuery({
    queryKey: ["evalRuns"],
    queryFn: async () => unwrap(await api.GET("/api/eval/runs")),
  });
}

export function useEvalRun(id: number | undefined) {
  return useQuery({
    queryKey: ["evalRun", id],
    queryFn: async () => unwrap(await api.GET("/api/eval/runs/{id}", { params: { path: { id: id! } } })),
    enabled: id !== undefined,
  });
}
