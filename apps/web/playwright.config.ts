import { defineConfig, devices } from "@playwright/test";

/**
 * Browser tests run against the production build with the API mocked at the network
 * layer (e2e/mock-api.ts): deterministic fixtures typed by the OpenAPI contract, no
 * backend or database. `pnpm build` must run first (CI does).
 */
const PORT = 3100;
// A second server with development sign-in off: production sign-in, as it really renders.
const PRODUCTION_PORT = 3101;
/**
 * `make prod-onboard` (P12.7): the onboarding journey against the running reference stack
 * (https://localhost:8443, the real API, worker and identity provider) -- only then, and
 * then alone: no local servers are started for it and no other project runs.
 */
const ONBOARD_STACK = !!process.env.LATTICE_ONBOARD_STACK;

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
  projects: ONBOARD_STACK
    ? [
        {
          name: "onboarding-stack",
          use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, baseURL: "https://localhost:8443", ignoreHTTPSErrors: true },
          testMatch: /onboarding-stack\.spec/,
        },
      ]
    : [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } }, testIgnore: /(screens|production|onboarding-stack)\.spec/ },
    { name: "mobile", use: { ...devices["Pixel 7"], viewport: { width: 390, height: 844 } }, testMatch: /(shell|responsive|mobile)\.spec/ },
    { name: "screens", use: { ...devices["Desktop Chrome"] }, testMatch: /screens\.spec/ },
    {
      name: "production",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, baseURL: `http://127.0.0.1:${PRODUCTION_PORT}` },
      testMatch: /production\.spec/,
    },
  ],
  webServer: ONBOARD_STACK ? undefined : [
    {
      command: `pnpm exec next start --port ${PORT} --hostname 127.0.0.1`,
      url: `http://127.0.0.1:${PORT}/sign-in`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        LATTICE_WEB_DEV_SIGN_IN: "true",
        // The design-system specimen (/specimen) is development-only; the tests need it.
        LATTICE_WEB_SPECIMEN: "true",
        // The two seeded organizations, plus a test-only manifest of long names.
        LATTICE_DEV_PERSONA_MANIFESTS: "../../data/synthetic/halden/org.yaml,../../data/synthetic/veridian/org.yaml,e2e/fixtures/long-names.yaml",
        // Never reached: every /api request is fulfilled by the test's mock.
        LATTICE_API_URL: "http://127.0.0.1:9",
      },
    },
    {
      command: `pnpm exec next start --port ${PRODUCTION_PORT} --hostname 127.0.0.1`,
      url: `http://127.0.0.1:${PRODUCTION_PORT}/sign-in`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        LATTICE_WEB_DEV_SIGN_IN: "false",
        // Set, to show they are ignored while development sign-in is off.
        LATTICE_DEV_PERSONA_MANIFESTS: "../../data/synthetic/halden/org.yaml",
        LATTICE_API_URL: "http://127.0.0.1:9",
      },
    },
  ],
});
