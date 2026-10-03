import { describe, expect, it } from "vitest";

import type { ProviderOut } from "@/api/types";
import { PROVIDERS } from "@/test/fixtures";

import { byPurpose, neverSent, readCell } from "./egress-model";

const [ollama, gemini, embed] = PROVIDERS.providers as [ProviderOut, ProviderOut, ProviderOut];
const egress = PROVIDERS.egress;

describe("egress lattice", () => {
  it("caps a local provider only by its own approval", () => {
    expect(readCell(egress, ollama, "confidential")).toEqual({
      permitted: true,
      because: "it runs inside the Lattice deployment, and it's approved up to Confidential",
    });
    expect(readCell(egress, ollama, "restricted")).toEqual({ permitted: false, because: "it's approved only up to Confidential" });
    expect(readCell(egress, embed, "restricted").permitted).toBe(true);
  });

  it("caps an external API by the policy, and never calls a disabled generation provider", () => {
    const enabled = { ...gemini, enabled: true, approved_for_level: "confidential" };
    expect(readCell(egress, enabled, "internal").permitted).toBe(true);
    expect(readCell(egress, enabled, "confidential")).toEqual({ permitted: false, because: "the AI data policy allows external APIs only up to Internal" });
    expect(readCell(egress, gemini, "internal")).toEqual({ permitted: false, because: "the provider is disabled, so it isn't called" });
  });

  it("fails closed for unknown classes, unapproved organization-controlled providers and data use", () => {
    expect(readCell(egress, { ...ollama, deployment: "private" }, "internal").permitted).toBe(false);
    const org = { ...ollama, deployment: "organization_controlled" };
    expect(readCell(egress, { ...org, explicitly_approved: false }, "internal").because).toBe("an organization-controlled provider needs an explicit approval first");
    expect(readCell(egress, org, "internal").because).toBe("the AI data policy doesn't allow organization-controlled providers");
    expect(readCell({ ...egress, organization_controlled_level: "confidential" }, org, "confidential").permitted).toBe(true);
    const customer = { ...egress, data_origin: "customer" };
    expect(readCell(customer, { ...gemini, enabled: true }, "internal").because).toBe("it may use submitted data, so it may receive synthetic data only");
  });

  it("is unchanged by a credential", () => {
    const withKey = { ...ollama, credential: { state: "active" as const, source: "env", resolvable: true, data_use: "paid" } };
    for (const level of ["internal", "confidential", "restricted"] as const) {
      expect(readCell(egress, withKey, level)).toEqual(readCell(egress, ollama, level));
    }
  });

  it("orders generation before embedding and finds what never leaves Lattice", () => {
    expect(byPurpose([embed, ollama, gemini]).map((g) => g.purpose)).toEqual(["generation", "embedding"]);
    expect(neverSent(egress, [ollama, gemini])).toEqual(["restricted"]);
    expect(neverSent(egress, PROVIDERS.providers)).toEqual([]);
  });
});
