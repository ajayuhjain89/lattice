/**
 * Words for the AI providers page (Phase 12, P12.5). Every cell, state, result and
 * refusal is the API's: this file only says them. It never decides what may be sent --
 * the egress lattice arrives computed by the server (GET /v1/admin/providers `lattice`),
 * and an escalation is the server's verdict on a preview.
 */
import { isApiError, retryPhrase } from "@/api/errors";
import type { Destination, EgressSettings, ProviderCredential, ProviderOut, ProviderTest, StepUpOutcome, Transition } from "@/api/types";
import { LEVELS, type Level } from "@/components/primitives/LevelMeter";
import { formatDayTime, levelLabel, trustClassLabel } from "@/lib/format";

const KNOWN_CLASSES = ["local", "organization_controlled", "external_api"];

export const providerKey = (p: ProviderOut) => p.id;
export const providerName = (p: { provider: string; model: string }) => `${p.provider} · ${p.model}`;

/** Purposes in reading order: what answers first, then what indexes. */
const PURPOSE_ORDER = ["generation", "embedding"];

export function byPurpose(providers: readonly ProviderOut[]): { purpose: string; providers: ProviderOut[] }[] {
  const groups = new Map<string, ProviderOut[]>();
  for (const p of providers) groups.set(p.purpose, [...(groups.get(p.purpose) ?? []), p]);
  const order = (k: string) => (PURPOSE_ORDER.includes(k) ? PURPOSE_ORDER.indexOf(k) : PURPOSE_ORDER.length);
  return [...groups.entries()].sort(([a], [b]) => order(a) - order(b) || a.localeCompare(b)).map(([purpose, list]) => ({ purpose, providers: list }));
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
      return "Runs on a server your organization controls, outside Lattice.";
    case "external_api":
      return "A third-party API, outside your organization.";
    default:
      return `Recorded as “${deployment}”, which Lattice doesn't recognise: nothing is sent.`;
  }
}

const CLASS_PLURAL: Record<string, string> = {
  external_api: "external APIs",
  organization_controlled: "organization-controlled servers",
};

export function ceilingOf(egress: EgressSettings, deployment: string): string | null {
  if (deployment === "external_api") return egress.external_egress_level ?? null;
  if (deployment === "organization_controlled") return egress.organization_controlled_level ?? null;
  return null;
}

/** Why a cell holds, as a clause ("it's approved only up to Confidential"), from the server's reason code. */
export function cellClause(reason: string, p: ProviderOut, egress: EgressSettings): string {
  const ceiling = ceilingOf(egress, p.deployment);
  const where = CLASS_PLURAL[p.deployment] ?? "this class";
  switch (reason) {
    case "permitted":
      return p.deployment === "local"
        ? `it runs inside the Lattice deployment, and it's approved up to ${levelLabel(p.approved_for_level)}`
        : `the AI data policy allows ${where} up to ${levelLabel(ceiling ?? "")}, and it's approved up to ${levelLabel(p.approved_for_level)}`;
    case "provider_disabled":
      return "the provider is disabled, so it isn't called";
    case "provider_unapproved":
      return p.deployment === "organization_controlled" && p.purpose !== "generation"
        ? "an organization-controlled provider needs an explicit approval first"
        : "it hasn't been approved for any level";
    case "deployment_unknown":
      return "its trust class isn't one Lattice recognises, so nothing is sent";
    case "provider_data_use":
      return "it may use submitted data, so it may receive synthetic data only";
    case "deployment_not_allowed":
      return `the AI data policy doesn't allow ${where}`;
    case "above_approval":
      return `it's approved only up to ${levelLabel(p.approved_for_level)}`;
    case "above_ceiling":
      return `the AI data policy allows ${where} only up to ${levelLabel(ceiling ?? "")}`;
    default:
      return "its approval isn't a level Lattice recognises, so nothing is sent";
  }
}

/** The status line of a record: what it may send now. */
export function sendsLine(p: ProviderOut): string {
  if (p.allowed_up_to) {
    return p.allowed_up_to === "restricted" ? "May receive content up to Restricted" : `Sends nothing above ${levelLabel(p.allowed_up_to)}`;
  }
  switch (p.blocked_by) {
    case "provider_disabled":
      return "Sends nothing: disabled";
    case "provider_unapproved":
      return "Sends nothing: not approved";
    case "deployment_not_allowed":
      return "Sends nothing: the AI data policy doesn't allow its trust class";
    case "provider_data_use":
      return "Sends nothing: it may use submitted data";
    case "deployment_unknown":
      return "Sends nothing: unrecognised trust class";
    default:
      return "Sends nothing";
  }
}

export function approvalLine(p: ProviderOut): string {
  if (p.purpose === "generation" && !p.explicitly_approved) return "Not approved: nothing is sent to it";
  if (!p.explicitly_approved) return `Default approval: up to ${levelLabel(p.approved_for_level)}`;
  return `Approved up to ${levelLabel(p.approved_for_level)}`;
}

export function destinationLine(d: Destination): string {
  switch (d.kind) {
    case "vendor_api":
      return d.origin ? `The provider's public API, ${d.origin}` : "The provider's public API";
    case "registered_target":
      return `${d.origin ?? "A server"}${d.access === "operator_approved" ? ", approved for this organization by your Lattice operator" : d.access === "public" ? ", a public address" : ""}`;
    case "deployment_local":
      return d.origin ? `${d.origin}, inside the Lattice deployment` : "Inside the Lattice deployment";
    case "test_double":
      return "A test double: no network, never in production";
  }
}

export function destinationBlocked(d: Destination): boolean {
  return d.access === "not_permitted";
}

/** Who holds the key, in a few words. */
export function credentialHeadline(c: ProviderCredential): string {
  if (c.state === "none") return "No API key: nothing can be called";
  return c.managed_by === "organization" ? (c.hint ? `Set in Lattice · ends ••••${c.hint}` : "Set in Lattice") : "Set by your Lattice operator in the deployment";
}

export function credentialLine(c: ProviderCredential, required: boolean): string {
  if (c.state === "none") return required ? "No API key: nothing can be called" : "None needed";
  const parts: string[] = [];
  if (c.set_at) parts.push(`Since ${formatDayTime(c.set_at)}`);
  if (!c.resolvable) parts.push("can't be opened");
  if (c.data_use) parts.push(c.data_use === "paid" ? "paid tier" : "unpaid tier: synthetic data only");
  return parts.join(" · ") || "Its value is never shown";
}

const TEST_RESULT: Record<ProviderTest["result"], string> = {
  valid: "Works",
  invalid_credential: "The provider rejected the API key",
  configuration_invalid: "The provider doesn't know this model",
  provider_unavailable: "The provider couldn't be reached",
  outbound_blocked: "Refused by Lattice's outbound policy",
  credential_missing: "No API key to test",
  unsupported: "Nothing to test",
};

export function testLine(test: ProviderTest | null | undefined): string {
  if (!test) return "Not tested";
  if (!test.current) return "Not tested since the key changed";
  return `${TEST_RESULT[test.result]} · ${formatDayTime(test.at)}`;
}

export function testResultMessage(result: ProviderTest["result"]): string {
  switch (result) {
    case "valid":
      return "The credential works for this provider. That's all a test shows: it approves nothing and lets nothing more leave Lattice.";
    case "invalid_credential":
      return "The provider rejected the API key. Replace it.";
    case "configuration_invalid":
      return "The provider answered, but doesn't know this model.";
    case "provider_unavailable":
      return "Lattice couldn't reach the provider. Try again later; nothing changed.";
    case "outbound_blocked":
      return "Lattice's outbound policy refuses this endpoint. A private or self-hosted server must be approved for this organization by your Lattice operator.";
    case "credential_missing":
      return "There's no API key to test. Add one first.";
    case "unsupported":
      return "This provider has nothing to test.";
  }
}

export function dataTermsLabel(mode: string, optOut: boolean): string {
  if (mode === "none") return "Keeps nothing Lattice sends";
  if (mode === "abuse_monitoring_limited") return "Paid terms: no training on submitted content";
  if (mode === "unpaid_product_improvement") return "Unpaid terms: may use submitted content";
  return optOut ? "Training opt-out" : "May use submitted data";
}

/** "Confidential content may now be sent to gemini · model (external API)". */
export function transitionSentence(t: Transition): string {
  const where = `${t.provider} · ${t.model} (${trustClassName(t.trust_class)}${t.destination ? `, ${t.destination}` : ""})`;
  return t.after ? `${levelLabel(t.level)} content may now be sent to ${where}.` : `${levelLabel(t.level)} content will no longer be sent to ${where}.`;
}

/** The restrictions that stay in force, from the preview's fixed codes. */
export function remainingSentence(code: string): string | null {
  const [name, value] = code.split(":");
  switch (name) {
    case "authorization_first":
      return "Each person's access still decides first: no model ever receives content the asker can't read.";
    case "question_floor":
      return `Questions are treated as at least ${levelLabel(value ?? "internal")}; askers can only raise that.`;
    case "external_not_allowed":
      return "External APIs receive nothing.";
    case "organization_controlled_not_allowed":
      return "Organization-controlled servers receive nothing.";
    case "never_sent":
      return `${levelLabel(value ?? "")} content is never sent to any AI provider.`;
    case "credentials_never_widen":
      return "API keys and passing tests never widen any of this.";
    default:
      return null;
  }
}

export function levelsList(levels: readonly string[]): string {
  const names = levels.map(levelLabel);
  return names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

export const isLevel = (value: string | null | undefined): value is Level => value != null && (LEVELS as readonly string[]).includes(value);

export const STEP_UP_OUTCOME: Record<StepUpOutcome, { title: string; message: string }> = {
  pending: { title: "Waiting for your sign-in", message: "Finish signing in at your identity provider. Nothing changes until you apply the change here." },
  granted: { title: "Signed in again", message: "Your identity provider confirmed a fresh sign-in. Apply the change within five minutes of it." },
  not_fresh: {
    title: "Not a fresh sign-in",
    message: "Your identity provider didn't confirm that you signed in just now (or it was more than five minutes ago). Nothing changed. Try again; if it keeps happening, your identity provider may be reusing an earlier sign-in.",
  },
  session_mismatch: { title: "Finished in another session", message: "The sign-in came back to a different Lattice session than the one that started it. Nothing changed." },
  assurance_insufficient: {
    title: "Sign-in method not strong enough",
    message: "Your Lattice operator requires a particular sign-in method for this, and your identity provider didn't confirm it. Nothing changed.",
  },
  step_up_invalid: { title: "Not accepted", message: "Lattice couldn't accept this sign-in for the change. Nothing changed. Start again." },
  mismatched: { title: "Signed in as someone else", message: "The identity provider signed in a different person than you. Only you can confirm your own change. Nothing changed." },
  not_provisioned: { title: "Not your identity", message: "The identity provider signed in someone who isn't a member here. Nothing changed." },
  sign_in_failed: { title: "Sign-in didn't finish", message: "The sign-in at your identity provider was cancelled or failed. Nothing changed." },
  token_invalid: { title: "Sign-in couldn't be verified", message: "Lattice couldn't verify the identity provider's answer. Nothing changed." },
  provider_unavailable: { title: "Identity provider unavailable", message: "Lattice couldn't reach your identity provider. Nothing changed. Try again later." },
  invalid_configuration: { title: "Identity provider misconfigured", message: "The identity provider's configuration doesn't match what Lattice expects. Nothing changed." },
  outbound_blocked: { title: "Identity provider refused", message: "Lattice's outbound policy refused the identity provider's address. Nothing changed." },
  credentials_rejected: { title: "Lattice's client was rejected", message: "The identity provider rejected Lattice's client credentials. Nothing changed." },
};

/** One sentence for a refused provider or policy change. */
export function providerError(error: unknown): string {
  if (!isApiError(error)) return "Something went wrong. Try again.";
  if (error.kind === "network") return error.message;
  if (error.status === 403) return "You no longer have administrator access.";
  if (error.status === 404) return "This is no longer available. Close this and reload the page.";
  if (error.isRateLimited) return `You're making changes quickly. Try again ${retryPhrase(error.retryAfter)}.`;
  const issue = error.issues;
  if (issue.includes("grant_consumed")) return "This change was already applied with that sign-in. Each sign-in confirms one change, once.";
  if (issue.includes("grant_expired")) return "More than five minutes passed since you signed in again. Review the change and sign in again.";
  if (issue.includes("grant_mismatch") || issue.includes("grant_invalid")) return "That sign-in confirmed a different change. Review the change and sign in again.";
  if (issue.includes("session_ended")) return "Your session ended. Sign in, review the change and confirm it again.";
  if (error.status === 409 && error.fields.includes("policy_version"))
    return "The AI data policy changed after you reviewed this. Close this, review the latest, and try again.";
  if (error.status === 409 && error.fields.includes("expected_credential_id")) return "The API key changed after you opened this. Close this and review the latest.";
  if (issue.includes("provider_exists")) return "This provider and model are already configured.";
  if (issue.includes("step_up_required")) return "This raises what may leave Lattice: it needs a fresh sign-in at your identity provider.";
  if (issue.includes("unavailable_no_connection"))
    return "Raising what may leave Lattice needs a fresh sign-in at your identity provider, and this organization doesn't sign in through one. Your Lattice operator can make the change.";
  if (issue.includes("unavailable_no_identity"))
    return "Raising what may leave Lattice needs a fresh sign-in at your identity provider, and you don't sign in through it. Ask another administrator, or your Lattice operator.";
  if (issue.includes("mismatch")) return "The confirmation doesn't match. Type the organization's name exactly as shown.";
  if (issue.includes("not_escalation")) return "This change doesn't raise anything: apply it directly.";
  if (issue.includes("secret_storage_unavailable"))
    return "Lattice can't store API keys in this deployment yet. Your Lattice operator needs to configure its encryption keys.";
  if (issue.includes("deployment_managed")) return "The embedding provider is your Lattice operator's: only its approval for this organization can change here.";
  if (issue.includes("data_terms_fixed")) return "This provider's data terms are fixed.";
  if (issue.includes("trust_class_change")) return "That server has a different trust class. Configure a new provider for it instead.";
  if (issue.includes("not_registered")) return "That server isn't approved for this organization. Your Lattice operator registers private servers.";
  if (issue.includes("not_https")) return "That server is reachable over plain http only. A server outside the Lattice deployment must use https.";
  if (issue.includes("credential_not_used")) return "This provider doesn't use an API key.";
  if (issue.includes("no_change")) return "Nothing would change.";
  return error.message;
}

export function ceilingWords(level: string | null | undefined): string {
  return level ? `Up to ${levelLabel(level)}` : "Not allowed";
}
