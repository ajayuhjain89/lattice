import type { ErrorBody, ErrorCode } from "./types";

/**
 * One normalized failure for every call. `kind` separates "the server answered with an
 * error envelope" from "the server could not be reached". Messages shown to people are
 * the server's fixed sentences or our own; nothing else is ever rendered.
 */
export class ApiError extends Error {
  readonly kind: "http" | "network";
  readonly status: number;
  readonly code: ErrorCode | null;
  readonly requestId: string | null;

  constructor(init: {
    kind: "http" | "network";
    status: number;
    code: ErrorCode | null;
    message: string;
    requestId: string | null;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.kind = init.kind;
    this.status = init.status;
    this.code = init.code;
    this.requestId = init.requestId;
  }

  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  /** 404 -- and 422 on a path identifier -- are "not available", indistinguishably. */
  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isForbidden(): boolean {
    return this.status === 403 && this.code === "permission_denied";
  }
}

const FALLBACK_MESSAGE = "Something went wrong. Try again, and contact support if it persists.";
export const NETWORK_MESSAGE = "Couldn't reach Lattice. Check your connection and try again.";

function isErrorBody(value: unknown): value is { error: ErrorBody } {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return (
    typeof error === "object" &&
    error !== null &&
    typeof (error as ErrorBody).code === "string" &&
    typeof (error as ErrorBody).message === "string"
  );
}

export function fromResponse(response: Response, body: unknown): ApiError {
  if (isErrorBody(body)) {
    return new ApiError({
      kind: "http",
      status: response.status,
      code: body.error.code,
      message: body.error.message,
      requestId: body.error.request_id ?? response.headers.get("x-request-id"),
    });
  }
  return new ApiError({
    kind: "http",
    status: response.status,
    code: null,
    message: FALLBACK_MESSAGE,
    requestId: response.headers.get("x-request-id"),
  });
}

export function networkError(): ApiError {
  return new ApiError({
    kind: "network",
    status: 0,
    code: null,
    message: NETWORK_MESSAGE,
    requestId: null,
  });
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

/** A failure that is neither an API answer nor a transport error (a bug): generic copy. */
export function unexpectedError(): ApiError {
  return new ApiError({ kind: "http", status: 0, code: null, message: FALLBACK_MESSAGE, requestId: null });
}
