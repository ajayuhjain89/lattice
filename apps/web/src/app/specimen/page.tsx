import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { SpecimenScreen } from "@/components/specimen/SpecimenScreen";

import { Providers } from "../providers";

export const metadata: Metadata = { title: "Specimen" };
export const dynamic = "force-dynamic";

/**
 * The design-system specimen (design-direction §8, D1): every token, type role, glyph and
 * layout primitive, in the shipped light theme and the unshipped dark map. Development
 * only -- a production build serves it only with LATTICE_WEB_SPECIMEN=true (the browser
 * tests set it, like development sign-in); otherwise it is an ordinary 404.
 */
export default function SpecimenPage() {
  if (process.env.NODE_ENV === "production" && process.env.LATTICE_WEB_SPECIMEN !== "true") notFound();
  return (
    <Providers>
      <SpecimenScreen />
    </Providers>
  );
}
