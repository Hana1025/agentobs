import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReviewForm } from "./ReviewQueue";
import type { TriageDto } from "@/api/hooks";

const triage: TriageDto = {
  id: 42,
  traceId: "a".repeat(32),
  projectId: "demo",
  triageVersion: "v1",
  rulesFired: ["retries_high"],
  state: {},
  triageCostUsd: 0,
  route: "escalate",
  reviewStatus: "pending",
  s1Label: "suspicious",
  s2RootCause: "timeout_cascade",
  createdAt: new Date().toISOString(),
};

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("ReviewForm", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("posts the review payload and calls onDone", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ review: { id: 1 }, evalCaseId: 9 }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    const onDone = vi.fn();
    wrap(<ReviewForm triage={triage} onDone={onDone} />);

    fireEvent.change(screen.getByLabelText("human_label"), { target: { value: "failed" } });
    fireEvent.change(screen.getByLabelText("human_root_cause"), { target: { value: "provider_failure" } });
    fireEvent.change(screen.getByLabelText("note"), { target: { value: "confirmed" } });
    fireEvent.submit(screen.getByTestId("review-form"));

    await waitFor(() => expect(onDone).toHaveBeenCalled());
    // openapi-fetch 传给 fetch 的是一个 Request 对象
    const req = fetchMock.mock.calls[0][0] as Request;
    expect(req.url).toContain("/api/reviews");
    expect(req.method).toBe("POST");
    expect(JSON.parse(await req.text())).toEqual({
      triage_id: 42,
      human_label: "failed",
      human_root_cause: "provider_failure",
      note: "confirmed",
    });
  });

  it("shows the error and keeps the form on failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "triage already reviewed" }), { status: 409, headers: { "Content-Type": "application/problem+json" } }),
    );
    const onDone = vi.fn();
    wrap(<ReviewForm triage={triage} onDone={onDone} />);
    fireEvent.submit(screen.getByTestId("review-form"));
    await waitFor(() => expect(screen.getByText(/already reviewed/)).toBeInTheDocument());
    expect(onDone).not.toHaveBeenCalled();
  });
});
