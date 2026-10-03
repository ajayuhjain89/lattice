import type { Metadata } from "next";

import { ProductionSignIn } from "@/components/sign-in/ProductionSignIn";
import { readSignIn, signInMetadata, type SignInSearch } from "@/components/sign-in/sign-in-page";

export const dynamic = "force-dynamic";

export function generateMetadata({ searchParams }: { searchParams: SignInSearch }): Promise<Metadata> {
  return signInMetadata(searchParams);
}

/**
 * `/sign-in`: the organization's identity provider, nothing to choose. The error is read
 * from a closed set (anything else is ignored) and `next` by the API's same-origin rule.
 * Where development sign-in is on, the proxy serves this address from
 * `/sign-in/development` instead (src/proxy.ts), so nothing of the persona picker is
 * imported here.
 */
export default async function SignInPage({ searchParams }: { searchParams: SignInSearch }) {
  const { error, next } = await readSignIn(searchParams);
  return <ProductionSignIn error={error} next={next} />;
}
