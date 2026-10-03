import { afterEach, describe, expect, it, vi } from "vitest";

import SpecimenPage from "./page";

describe("/specimen", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("is a 404 in a production build unless explicitly enabled", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("LATTICE_WEB_SPECIMEN", "");
    expect(() => SpecimenPage()).toThrow(/NEXT_HTTP_ERROR_FALLBACK;404/);
  });

  it("renders in development and when enabled", () => {
    vi.stubEnv("NODE_ENV", "development");
    expect(() => SpecimenPage()).not.toThrow();
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("LATTICE_WEB_SPECIMEN", "true");
    expect(() => SpecimenPage()).not.toThrow();
  });
});
