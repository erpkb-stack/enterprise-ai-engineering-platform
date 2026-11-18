import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { MockBadge, ScoreBar, StatusBadge } from "@/components/common";
import { GroundingView } from "@/features/ExecutionView";
import type { Grounding } from "@/types";

describe("trust & status components", () => {
  it("labels mock vs live model output", () => {
    const { rerender } = render(<MockBadge mock />);
    expect(screen.getByText("MOCK")).toBeInTheDocument();
    rerender(<MockBadge mock={false} />);
    expect(screen.getByText("LIVE MODEL")).toBeInTheDocument();
  });
  it("renders statuses and scores", () => {
    render(<><StatusBadge status="max_steps_exceeded" /><ScoreBar value={0.42} /></>);
    expect(screen.getByText("max steps exceeded")).toBeInTheDocument();
    expect(screen.getByText("42%")).toBeInTheDocument();
  });
});

const g: Grounding = {
  method: "deterministic-lexical-support-v1", threshold: 0.55, claim_count: 2, total_citations: 2, valid_citations: 1, nonexistent_citations: 0,
  citation_precision: 0.5, citation_coverage: 1, groundedness: 0.5, hallucination_rate: 0.5,
  claims: [
    { claim: "Refunds within 30 days", citations: [1], valid_citations: [1], invalid_citations: [], best_passage: 1, best_support: 1, supported: true },
    { claim: "Warranty is ninety years", citations: [1], valid_citations: [], invalid_citations: [{ citation: 1, reason: "passage does not support claim" }], best_passage: 1, best_support: 0.1, supported: false },
  ],
};

describe("GroundingView", () => {
  it("shows verified and rejected citations and reveals the cited passage", async () => {
    render(<GroundingView g={g} passages={["Customers may request a refund within 30 days."]} files={["refund.md"]} />);
    expect(screen.getByText("[1] verified")).toBeInTheDocument();
    expect(screen.getByText(/passage does not support claim/)).toBeInTheDocument();
    expect(screen.getByLabelText("unsupported")).toBeInTheDocument();
    await userEvent.click(screen.getByText("[1] verified"));
    expect(screen.getByText("Customers may request a refund within 30 days.")).toBeInTheDocument();
  });
  it("handles missing grounding", () => {
    render(<GroundingView g={null} />);
    expect(screen.getByText(/grounding not applicable/)).toBeInTheDocument();
  });
});
