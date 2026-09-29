import type { Metadata } from "next";

import { SignIn } from "@/components/sign-in/SignIn";
import { devSignInEnabled, loadDevPersonas } from "@/lib/dev-personas.server";

export const metadata: Metadata = { title: "Sign in" };
export const dynamic = "force-dynamic";

export default async function SignInPage() {
  const enabled = devSignInEnabled();
  const organizations = enabled ? await loadDevPersonas() : [];
  return <SignIn devEnabled={enabled} organizations={organizations} />;
}
