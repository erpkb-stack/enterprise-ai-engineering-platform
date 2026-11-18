import { defineConfig } from "@playwright/test";

/** E2E against a running stack: `make dev` (or docker compose) then `npm run test:e2e`. */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5180", trace: "retain-on-failure" },
  reporter: [["list"]],
});
