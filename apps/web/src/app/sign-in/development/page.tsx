import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { DevSignInRoot } from "@/components/sign-in/DevSignInRoot";
import { readSignIn, signInMetadata, type SignInSearch } from "@/components/sign-in/sign-in-page";
import { loadDevPersonas } from "@/lib/dev-personas.server";
import { devSignInEnabled } from "@/lib/dev-sign-in";
import { afterSignIn } from "@/lib/next-path";

export const dynamic = "force-dynamic";

export function generateMetadata({ searchParams }: { searchParams: SignInSearch }): Promise<Metadata> {
  return signInMetadata(searchParams);
}

/**
 * Development sign-in, served at `/sign-in` by the proxy when LATTICE_WEB_DEV_SIGN_IN=true.
 * Off, this route is an ordinary 404, whoever asks for it; the API refuses the sign-in
 * outside development and test whatever this page shows.
 */
export default async function DevSignInPage({ searchParams }: { searchParams: SignInSearch }) {
  if (!devSignInEnabled()) notFound();
  const { error, next, rawNext } = await readSignIn(searchParams);
  const organizations = await loadDevPersonas();
  return <DevSignInRoot error={error} next={next} destination={afterSignIn(rawNext)} organizations={organizations} />;
}
