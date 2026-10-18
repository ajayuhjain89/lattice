/**
 * Upload an export to a source (POST /v1/admin/sources/{id}/imports). The body is the file
 * itself, streamed by the browser; XMLHttpRequest is used (not fetch) because only it
 * reports how many bytes have been sent. That count is the only progress shown: once the
 * last byte is sent the server validates the archive, and nothing about that is measured
 * here -- the caller shows an honest "validating" state until the response arrives.
 *
 * Same origin, the session cookie, the CSRF proof header the API requires, and the API's
 * own error envelope -- exactly like every other call (api/client.ts).
 */
import { fromResponse, networkError, unexpectedError } from "@/api/errors";
import type { UploadResult } from "@/api/types";

export interface UploadCallbacks {
  /** Bytes of the file sent so far, and its size. */
  onProgress?: (sent: number, total: number) => void;
  /** Every byte is sent: the server is now validating. */
  onSent?: () => void;
  signal?: AbortSignal;
}

/** The media type the API documents for the file (it decides the format by content). */
export function uploadContentType(file: File): string {
  return /\.jsonl$/i.test(file.name) || file.type === "application/x-ndjson" ? "application/x-ndjson" : "application/zip";
}

export function uploadExport(sourceId: string, file: File, { onProgress, onSent, signal }: UploadCallbacks = {}): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("The upload was cancelled.", "AbortError"));
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${window.location.origin}/api/v1/admin/sources/${encodeURIComponent(sourceId)}/imports`);
    xhr.withCredentials = false; // same origin: the cookie travels anyway
    xhr.responseType = "text";
    xhr.setRequestHeader("X-Lattice-Request", "1");
    xhr.setRequestHeader("Content-Type", uploadContentType(file));
    xhr.upload.onprogress = (event) => onProgress?.(event.loaded, event.lengthComputable ? event.total : file.size);
    xhr.upload.onload = () => onSent?.();
    xhr.onload = () => {
      let body: unknown = undefined;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : undefined;
      } catch {
        body = undefined;
      }
      if (xhr.status >= 200 && xhr.status < 300 && body && typeof body === "object") {
        resolve(body as UploadResult);
        return;
      }
      if (xhr.status < 200 || xhr.status > 599) {
        reject(unexpectedError());
        return;
      }
      const headers = new Headers();
      for (const name of ["retry-after", "x-request-id"]) {
        const value = xhr.getResponseHeader(name);
        if (value) headers.set(name, value);
      }
      reject(fromResponse(new Response(null, { status: xhr.status, headers }), body));
    };
    xhr.onerror = () => reject(networkError());
    xhr.ontimeout = () => reject(networkError());
    xhr.onabort = () => reject(new DOMException("The upload was cancelled.", "AbortError"));
    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(file);
  });
}
