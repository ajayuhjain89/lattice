import "server-only";

import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { parse } from "yaml";

/**
 * Development sign-in only. The persona picker lists the members of the seed manifests
 * named by LATTICE_DEV_PERSONA_MANIFESTS (comma-separated paths), read on the server at
 * request time -- never bundled into the client and never shipped as fixed data. Only
 * what a picker needs is kept: email, display name, role label, organization. Nothing
 * about access (clearance, accounts, departments) leaves this module.
 */

export interface DevPersona {
  email: string;
  displayName: string;
  roleLabel: string | null;
}

export interface DevOrganization {
  slug: string;
  name: string;
  personas: DevPersona[];
}

export function devSignInEnabled(): boolean {
  return process.env.LATTICE_WEB_DEV_SIGN_IN === "true";
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null;

export async function loadDevPersonas(): Promise<DevOrganization[]> {
  const paths = (process.env.LATTICE_DEV_PERSONA_MANIFESTS ?? "")
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
  const out: DevOrganization[] = [];
  for (const path of paths) {
    try {
      const doc: unknown = parse(await readFile(resolve(/* turbopackIgnore: true */ process.cwd(), path), "utf8"));
      if (!isObj(doc) || !isObj(doc.organization) || !Array.isArray(doc.members)) continue;
      const { slug, name } = doc.organization;
      if (typeof slug !== "string" || typeof name !== "string") continue;
      const personas = doc.members.flatMap((m): DevPersona[] =>
        isObj(m) && typeof m.email === "string" && typeof m.display_name === "string"
          ? [{ email: m.email, displayName: m.display_name, roleLabel: typeof m.role_label === "string" ? m.role_label : null }]
          : [],
      );
      out.push({ slug, name, personas });
    } catch {
      // A missing or unreadable manifest just means no shortcuts; the email form remains.
    }
  }
  return out;
}
