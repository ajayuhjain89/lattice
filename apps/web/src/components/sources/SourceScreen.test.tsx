import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { UploadResult } from "@/api/types";
import { ME_LENA, ME_MAYA, ORG_SOURCE, SOURCE, SOURCE_RECORD_DRIVE, SOURCE_RECORD_ESCALATIONS, SOURCE_RECORD_NEW, SOURCES_ADMIN, SOURCES_ADMIN_ORG, UPLOAD_ACCEPTED } from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { SourceScreen } from "./SourceScreen";
import type { UploadCallbacks } from "./upload";

// The upload's transport is upload.test.ts's; here, the states the record shows for it.
const upload = vi.hoisted(() => ({ calls: [] as { callbacks: UploadCallbacks; resolve: (r: UploadResult) => void; reject: (e: unknown) => void }[] }));
vi.mock("./upload", () => ({
  uploadExport: (_id: string, _file: File, callbacks: UploadCallbacks) =>
    new Promise<UploadResult>((resolve, reject) => upload.calls.push({ callbacks, resolve, reject })),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  upload.calls.length = 0;
});

const REGISTER = { sources: [...SOURCES_ADMIN.sources, ...SOURCES_ADMIN_ORG] };
const routes = {
  "GET /v1/admin/sources": [200, REGISTER],
  [`GET /v1/admin/sources/${ORG_SOURCE.escalations}`]: [200, SOURCE_RECORD_ESCALATIONS],
  [`GET /v1/admin/sources/${ORG_SOURCE.handbook}`]: [200, SOURCE_RECORD_NEW],
  [`GET /v1/admin/sources/${SOURCE.drive}`]: [200, SOURCE_RECORD_DRIVE],
} as Record<string, [number, unknown]>;

describe("SourceScreen", () => {
  it("shows the check's counts and references, never content, and the next step", async () => {
    mockFetch(routes);
    renderInWorkspace(<SourceScreen id={ORG_SOURCE.escalations} />, { me: ME_LENA });
    expect(await screen.findByRole("heading", { level: 1, name: "Halden Escalations" })).toBeInTheDocument();
    expect(screen.getByTestId("next-step")).toHaveTextContent("Map account references.");
    const check = screen.getByTestId("check-result");
    expect(within(check).getByText("Held back").closest("[data-attention]")).not.toBeNull();
    expect(check).toHaveTextContent("360009913");
    expect(screen.getByTestId("settings")).toHaveTextContent("Every record must belong to an account");
    expect(screen.getByTestId("current-import")).toHaveTextContent("3 files read");
  });

  it("upload: bytes sent, then validating with no number, then accepted", async () => {
    const user = userEvent.setup();
    mockFetch(routes);
    renderInWorkspace(<SourceScreen id={ORG_SOURCE.handbook} />, { me: ME_LENA });
    const input = await screen.findByLabelText("Export file");
    await user.upload(input, new File([new Uint8Array(4096)], "handbook.zip", { type: "application/zip" }));
    await user.click(screen.getByRole("button", { name: "Upload" }));
    const status = screen.getByTestId("upload-status");
    expect(status).toHaveTextContent("Upload started.");
    const call = upload.calls[0]!;
    act(() => call.callbacks.onProgress?.(1024 * 1024, 4 * 1024 * 1024));
    expect(screen.getByText("Sending 1 MiB of 4 MiB (25%)")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Upload progress" })).toHaveAttribute("aria-valuetext", "25% sent");
    act(() => call.callbacks.onSent?.());
    expect(status).toHaveTextContent("Received. Lattice is validating the archive");
    expect(screen.queryByRole("progressbar")).toBeNull();
    await act(async () => call.resolve(UPLOAD_ACCEPTED));
    expect(status).toHaveTextContent("Export accepted: 12 files kept, 2 ignored. A check is queued.");
  });

  it("refuses a file over the deployment's limit before sending it", async () => {
    const user = userEvent.setup();
    mockFetch(routes);
    renderInWorkspace(<SourceScreen id={ORG_SOURCE.handbook} />, { me: ME_LENA });
    const input = await screen.findByLabelText("Export file");
    const big = new File([new Uint8Array(8)], "big.zip", { type: "application/zip" });
    Object.defineProperty(big, "size", { value: 101 * 1024 * 1024 });
    await user.upload(input, big);
    expect(screen.getByText("This file is 101 MiB: the limit is 100 MiB.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Upload" }));
    expect(upload.calls).toHaveLength(0);
  });

  it("an operator's source names the operator workflow and offers no upload", async () => {
    mockFetch(routes);
    renderInWorkspace(<SourceScreen id={SOURCE.drive} />, { me: ME_LENA });
    expect(await screen.findByTestId("operator-managed")).toHaveTextContent("lattice sync");
    expect(screen.queryByLabelText("Export file")).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit settings" })).toBeNull();
  });

  it("members get the administrators-only page and never ask for the record", async () => {
    const calls = mockFetch(routes);
    renderInWorkspace(<SourceScreen id={ORG_SOURCE.escalations} />, { me: ME_MAYA });
    expect(await screen.findByRole("heading", { name: "You need administrator access to view this page" })).toBeInTheDocument();
    expect(calls.filter((c) => c.url.startsWith("/v1/admin/sources/"))).toHaveLength(0);
  });
});
