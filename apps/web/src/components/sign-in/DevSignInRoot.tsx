"use client";

import type { ComponentProps } from "react";

import { Providers } from "@/app/providers";

import { DevSignIn } from "./DevSignIn";

/** Development sign-in with the client providers it needs (the sign-in mutation). */
export function DevSignInRoot(props: ComponentProps<typeof DevSignIn>) {
  return (
    <Providers>
      <DevSignIn {...props} />
    </Providers>
  );
}
