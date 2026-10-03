/**
 * The egress lattice (design-direction §6.9): which data levels each recorded provider may
 * receive, read from GET /v1/admin/providers alone, in the order the server decides
 * (backend `providers/egress.py`, `answering/policy.py`):
 *
 *   1. a disabled generation provider is never called;
 *   2. an unrecognised trust class sends nothing (fail closed);
 *   3. an organization-controlled provider needs an explicit approval;
 *   4. a provider that may use submitted data receives synthetic data only;
 *   5. the class must be allowed, with a ceiling (external APIs and organization-controlled
 *      providers by the policy; local ones only by their own approval);
 *   6. a level may be sent up to min(ceiling, approval).
 *
 * A credential is not an input: it never widens what a provider may receive. The server
 * also refuses a provider whose implementation runs as a different class than recorded;
 * that check needs facts the API doesn't expose, so the lattice is what the recorded
 * configuration allows -- never more.
 */
import type { EgressOut, ProviderOut } from "@/api/types";
import { LEVELS, type Level } from "@/components/primitives/LevelMeter";
import { levelLabel, trustClassLabel } from "@/lib/format";

const rank = (level: string) => LEVELS.indexOf(level as Level);
const known = (level: string | null | undefined): level is Level => level != null && rank(level) >= 0;

/** The organization's ceiling for a provider's class, or null when the class isn't allowed. */
export function classCeiling(egress: EgressOut, deployment: string): Level | null {
  if (deployment === "external_api") return known(egress.external_egress_level) ? egress.external_egress_level : null;
  if (deployment === "organization_controlled") return known(egress.organization_controlled_level) ? egress.organization_controlled_level : null;
  if (deployment === "local") return "restricted";
  return null;
}

const KNOWN_CLASSES = ["local", "organization_controlled", "external_api"];

export interface CellReading {
  permitted: boolean;
  /** Why, as a clause: "it's approved only up to Confidential". */
  because: string;
}

const CLASS_NAME: Record<string, string> = {
  external_api: "external APIs",
  organization_controlled: "organization-controlled providers",
};

export function readCell(egress: EgressOut, p: ProviderOut, level: Level): CellReading {
  if (p.purpose === "generation" && !p.enabled) return { permitted: false, because: "the provider is disabled, so it isn't called" };
  if (!KNOWN_CLASSES.includes(p.deployment)) {
    return { permitted: false, because: "its trust class isn't one Lattice recognises, so nothing is sent" };
  }
  if (p.deployment === "organization_controlled" && !p.explicitly_approved) {
    return { permitted: false, because: "an organization-controlled provider needs an explicit approval first" };
  }
  if (!p.training_opt_out && egress.data_origin !== "synthetic") {
    return { permitted: false, because: "it may use submitted data, so it may receive synthetic data only" };
  }
  const ceiling = classCeiling(egress, p.deployment);
  if (ceiling === null) return { permitted: false, because: `the AI data policy doesn't allow ${CLASS_NAME[p.deployment] ?? "this class"}` };
  if (!known(p.approved_for_level)) return { permitted: false, because: "its approval isn't a level Lattice recognises, so nothing is sent" };
  const effective = rank(ceiling) <= rank(p.approved_for_level) ? ceiling : p.approved_for_level;
  if (rank(level) <= rank(effective)) {
    const where =
      p.deployment === "local"
        ? "it runs inside the Lattice deployment"
        : `the AI data policy allows ${CLASS_NAME[p.deployment]} up to ${levelLabel(ceiling)}`;
    return { permitted: true, because: `${where}, and it's approved up to ${levelLabel(p.approved_for_level)}` };
  }
  if (rank(p.approved_for_level) < rank(level) && rank(p.approved_for_level) <= rank(ceiling)) {
    return { permitted: false, because: `it's approved only up to ${levelLabel(p.approved_for_level)}` };
  }
  return { permitted: false, because: `the AI data policy allows ${CLASS_NAME[p.deployment]} only up to ${levelLabel(ceiling)}` };
}

export const providerKey = (p: ProviderOut) => `${p.purpose}|${p.provider}|${p.model}`;

/** Purposes in reading order: what answers first, then what indexes. */
const PURPOSE_ORDER = ["generation", "embedding"];

export function byPurpose(providers: readonly ProviderOut[]): { purpose: string; providers: ProviderOut[] }[] {
  const groups = new Map<string, ProviderOut[]>();
  for (const p of providers) groups.set(p.purpose, [...(groups.get(p.purpose) ?? []), p]);
  const order = (k: string) => (PURPOSE_ORDER.includes(k) ? PURPOSE_ORDER.indexOf(k) : PURPOSE_ORDER.length);
  return [...groups.entries()].sort(([a], [b]) => order(a) - order(b) || a.localeCompare(b)).map(([purpose, list]) => ({ purpose, providers: list }));
}

/** The levels no provider may receive: they never leave Lattice. */
export function neverSent(egress: EgressOut, providers: readonly ProviderOut[]): Level[] {
  return LEVELS.filter((level) => providers.every((p) => !readCell(egress, p, level).permitted));
}

/** A trust class's name; anything unrecognised is named as such (it fails closed). */
export function trustClassName(deployment: string): string {
  return KNOWN_CLASSES.includes(deployment) ? trustClassLabel(deployment) : "Unrecognised";
}

/** One sentence on what a trust class means (D-15). */
export function trustClassMeaning(deployment: string): string {
  switch (deployment) {
    case "local":
      return "Runs inside the Lattice deployment; nothing leaves it.";
    case "organization_controlled":
      return "Runs on infrastructure your organization controls, outside Lattice.";
    case "external_api":
      return "A third-party API, outside your organization.";
    default:
      return `Recorded as “${deployment}”, which Lattice doesn't recognise: nothing is sent.`;
  }
}
