import type { Metadata } from "next";

import { DevSignInRoot } from "@/components/sign-in/DevSignInRoot";
import { ProductionSignIn } from "@/components/sign-in/ProductionSignIn";
import { continuation, SSO_ERROR_TITLES, ssoError } from "@/components/sign-in/sign-in-model";
import { devSignInEnabled, loadDevPersonas } from "@/lib/dev-personas.server";
import { afterSignIn } from "@/lib/next-path";

export const dynamic = "force-dynamic";

type Search = Promise<Record<string, string | string[] | undefined>>;

const one = (value: string | string[] | undefined) => (typeof value === "string" ? value : null);

/** An error is in the title too, so it is heard on arrival without moving focus. */
export async function generateMetadata({ searchParams }: { searchParams: Search }): Promise<Metadata> {
  const error = ssoError(one((await searchParams).error));
  return { title: error ? `${SSO_ERROR_TITLES[error]} · Sign in` : "Sign in", robots: { index: false, follow: false } };
}

/**
 * `/sign-in`. Production: the organization's identity provider, nothing to choose. The
 * development persona picker renders only when this server enables it
 * (LATTICE_WEB_DEV_SIGN_IN), and the API refuses its sign-in outside development and test
 * whatever the page shows. The error and `next` are read here, on the server: the error
 * from a closed set (anything else is ignored), `next` by the same-origin rule as the API.
 */
export default async function SignInPage({ searchParams }: { searchParams: Search }) {
  const params = await searchParams;
  const error = ssoError(one(params.error));
  const next = continuation(one(params.next));
  if (!devSignInEnabled()) return <ProductionSignIn error={error} next={next} />;
  const organizations = await loadDevPersonas();
  return <DevSignInRoot error={error} next={next} destination={afterSignIn(one(params.next))} organizations={organizations} />;
}
