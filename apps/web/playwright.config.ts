import { defineConfig, devices } from "@playwright/test";

/**
 * Browser tests run against the production build with the API mocked at the network
 * layer (e2e/mock-api.ts): deterministic fixtures typed by the OpenAPI contract, no
 * backend or database. `pnpm build` must run first (CI does).
 */
const PORT = 3100;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : 3,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    timezoneId: "UTC",
    locale: "en-GB",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } }, testIgnore: /screens\.spec/ },
    { name: "mobile", use: { ...devices["Pixel 7"], viewport: { width: 390, height: 844 } }, testMatch: /(shell|responsive|mobile)\.spec/ },
    { name: "screens", use: { ...devices["Desktop Chrome"] }, testMatch: /screens\.spec/ },
  ],
  webServer: {
    command: `pnpm exec next start --port ${PORT} --hostname 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}/sign-in`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: {
      LATTICE_WEB_DEV_SIGN_IN: "true",
      // The design-system specimen (/specimen) is development-only; the tests need it.
      LATTICE_WEB_SPECIMEN: "true",
      LATTICE_DEV_PERSONA_MANIFESTS: "../../data/synthetic/halden/org.yaml,../../data/synthetic/veridian/org.yaml",
      // Never reached: every /api request is fulfilled by the test's mock.
      LATTICE_API_URL: "http://127.0.0.1:9",
    },
  },
});
