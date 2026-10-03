/**
 * Whether this web server offers the development persona sign-in
 * (LATTICE_WEB_DEV_SIGN_IN=true; the API independently refuses it outside development and
 * test). Read at request time. When it is on, the proxy serves `/sign-in` from
 * DEV_SIGN_IN_ROUTE, so production sign-in never ships the persona picker's code.
 */
export const DEV_SIGN_IN_ROUTE = "/sign-in/development";

export function devSignInEnabled(): boolean {
  return process.env.LATTICE_WEB_DEV_SIGN_IN === "true";
}
