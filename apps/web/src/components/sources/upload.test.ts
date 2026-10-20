import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/errors";

import { uploadContentType, uploadExport } from "./upload";

/** A minimal XMLHttpRequest the test drives by hand. */
class FakeXhr {
  static last: FakeXhr;
  method = "";
  url = "";
  headers: Record<string, string> = {};
  body: unknown;
  status = 0;
  responseText = "";
  responseType = "";
  withCredentials = true;
  responseHeaders: Record<string, string> = {};
  upload: { onprogress?: (e: { loaded: number; total: number; lengthComputable: boolean }) => void; onload?: () => void } = {};
  onload?: () => void;
  onerror?: () => void;
  ontimeout?: () => void;
  onabort?: () => void;
  constructor() {
    FakeXhr.last = this;
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  getResponseHeader(name: string) {
    return this.responseHeaders[name] ?? null;
  }
  send(body: unknown) {
    this.body = body;
  }
  abort() {
    this.onabort?.();
  }
  answer(status: number, body: unknown, headers: Record<string, string> = {}) {
    this.status = status;
    this.responseText = JSON.stringify(body);
    this.responseHeaders = headers;
    this.onload?.();
  }
}

afterEach(() => vi.unstubAllGlobals());

const file = (name: string, size = 10) => new File([new Uint8Array(size)], name, { type: "" });

describe("uploadExport", () => {
  it("streams the file itself with the CSRF header, reports bytes sent, then resolves", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const progress: [number, number][] = [];
    let sent = false;
    const done = uploadExport("abc", file("tickets.zip", 100), { onProgress: (a, b) => progress.push([a, b]), onSent: () => (sent = true) });
    const xhr = FakeXhr.last;
    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe(`${window.location.origin}/api/v1/admin/sources/abc/imports`);
    expect(xhr.headers).toEqual({ "X-Lattice-Request": "1", "Content-Type": "application/zip" });
    expect(xhr.body).toBeInstanceOf(File);
    xhr.upload.onprogress?.({ loaded: 40, total: 100, lengthComputable: true });
    xhr.upload.onprogress?.({ loaded: 100, total: 0, lengthComputable: false });
    xhr.upload.onload?.();
    expect(progress).toEqual([
      [40, 100],
      [100, 100],
    ]);
    expect(sent).toBe(true);
    xhr.answer(201, { import: { id: "i" }, check_queued: true });
    await expect(done).resolves.toEqual({ import: { id: "i" }, check_queued: true });
  });

  it("turns the API's error envelope into an ApiError with its issue codes", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const done = uploadExport("abc", file("x.zip"));
    FakeXhr.last.answer(413, { error: { code: "request_too_large", message: "Too large.", request_id: "r1", details: [{ field: "import", issue: "too_large" }] } }, { "retry-after": "5" });
    const error = (await done.catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(413);
    expect(error.issues).toEqual(["too_large"]);
    expect(error.retryAfter).toBe(5);
  });

  it("reports a network failure and a cancellation distinctly", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const failed = uploadExport("abc", file("x.zip"));
    FakeXhr.last.onerror?.();
    expect(((await failed.catch((e: unknown) => e)) as ApiError).kind).toBe("network");
    const controller = new AbortController();
    const cancelled = uploadExport("abc", file("x.zip"), { signal: controller.signal });
    controller.abort();
    expect(((await cancelled.catch((e: unknown) => e)) as DOMException).name).toBe("AbortError");
  });

  it("labels a JSON Lines page as such (the server decides by content anyway)", () => {
    expect(uploadContentType(file("export.jsonl"))).toBe("application/x-ndjson");
    expect(uploadContentType(file("export.zip"))).toBe("application/zip");
  });
});
