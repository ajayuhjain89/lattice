import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Callout } from "@/components/primitives/Callout";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";

import { PageFault } from "./PageFault";

describe("RuleSkeleton", () => {
  it("names what is loading once and hides its rules", () => {
    const { container } = render(<RuleSkeleton variant="ledger" rows={4} label="Loading the audit log" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the audit log");
    expect(container.firstElementChild).toHaveAttribute("aria-busy", "true");
    expect(container.querySelectorAll('[aria-hidden="true"]')).toHaveLength(4);
  });
});

describe("Callout without a label", () => {
  it("is a single message in its role, with a decorative icon", () => {
    const { container } = render(
      <Callout tone="fault" role="alert" icon={<svg />}>
        Sign-in didn&apos;t complete.
      </Callout>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Sign-in didn't complete.");
    expect(container.querySelector("section")).toBeNull();
    expect(container.querySelector('[aria-hidden="true"] svg')).not.toBeNull();
  });
});

describe("PageFault", () => {
  it("shows nothing from the error, only the digest as a reference, and focuses itself", async () => {
    const retry = vi.fn();
    render(<PageFault digest="3021954872" onRetry={retry} home={{ href: "/ask", label: "Go to Ask", place: "Ask" }} />);
    expect(screen.getByRole("heading", { level: 1, name: "This page couldn't be shown" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Reference 3021954872");
    expect(screen.getByRole("alert")).toHaveTextContent("start again from Ask.");
    expect(screen.getByTestId("page-fault")).toHaveFocus();
    expect(screen.getByRole("link", { name: "Go to Ask" })).toHaveAttribute("href", "/ask");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("has no reference when the failure happened in the browser", () => {
    render(<PageFault onRetry={() => {}} home={{ href: "/", label: "Go to the start page", place: "the start page" }} />);
    expect(screen.getByRole("alert")).not.toHaveTextContent("Reference");
  });
});
