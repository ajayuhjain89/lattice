import type { Metadata } from "next";

import { continuation, SSO_ERROR_TITLES, ssoError, type Continuation, type SsoError } from "./sign-in-model";

/** What both sign-in routes read from the address, on the server. */
export type SignInSearch = Promise<Record<string, string | string[] | undefined>>;

export const one = (value: string | string[] | undefined) => (typeof value === "string" ? value : null);

export async function readSignIn(searchParams: SignInSearch): Promise<{ error: SsoError | null; next: Continuation | null; rawNext: string | null }> {
  const params = await searchParams;
  return { error: ssoError(one(params.error)), next: continuation(one(params.next)), rawNext: one(params.next) };
}

/** An error is in the title too, so it is heard on arrival without moving focus. */
export async function signInMetadata(searchParams: SignInSearch): Promise<Metadata> {
  const { error } = await readSignIn(searchParams);
  return { title: error ? `${SSO_ERROR_TITLES[error]} · Sign in` : "Sign in", robots: { index: false, follow: false } };
}
