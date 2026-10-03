import type { Metadata } from "next";

import { Landing } from "@/components/landing/Landing";

/**
 * `/`: the public landing page. Static content -- it calls no API and doesn't look for a
 * session, so a signed-in person sees the same page and goes on with Sign in. Not indexed
 * until there is an intentional public deployment (design-direction §10, decision 1), so
 * there is no canonical URL or social card to point at yet.
 */
export const metadata: Metadata = {
  title: { absolute: "Lattice — answers you can follow back to the page" },
  description:
    "Lattice answers questions from your organization's documents, tickets and conversations, searching only what you are allowed to read and tying every statement to the passage it came from.",
  robots: { index: false, follow: false },
};

export default function Home() {
  return <Landing />;
}
