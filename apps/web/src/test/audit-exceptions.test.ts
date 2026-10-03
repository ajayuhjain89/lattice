/**
 * The dependency audit's exceptions stay an explicit allowlist (docs/runbook.md §7):
 * exactly the advisories named here, each also recorded in the runbook, and no setting
 * or flag that would silence advisories wholesale.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { parse } from "yaml";

const WEB = join(__dirname, "..", "..");
const REPO = join(WEB, "..", "..");

// Changing this list is a reviewed security decision: add the runbook entry with it.
const ALLOWED_GHSAS = ["GHSA-vfj7-8cjw-p6xm"];

const workspace = parse(readFileSync(join(WEB, "pnpm-workspace.yaml"), "utf8")) as Record<string, unknown>;
const pkg = JSON.parse(readFileSync(join(WEB, "package.json"), "utf8")) as Record<string, unknown>;
const makefile = readFileSync(join(REPO, "Makefile"), "utf8");
const runbook = readFileSync(join(REPO, "docs", "runbook.md"), "utf8");

describe("dependency-audit exceptions", () => {
  it("ignore exactly the allowlisted advisories, by GHSA, and nothing else", () => {
    expect(workspace.auditConfig).toEqual({ ignoreGhsas: ALLOWED_GHSAS });
    expect(pkg).not.toHaveProperty("pnpm");
  });

  it("are each documented in the runbook", () => {
    for (const ghsa of ALLOWED_GHSAS) expect(runbook).toContain(ghsa);
  });

  it("leave the audit command at full strength", () => {
    const commands = makefile
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.includes("pnpm audit") && !line.startsWith("#"));
    expect(commands).toEqual(["$(WEB) pnpm audit --audit-level=low"]);
  });
});
