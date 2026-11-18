import { expect, test } from "@playwright/test";

const PASSWORD = process.env.AIECC_DEMO_PASSWORD ?? "Aiecc!Demo2026";

test("login, run the flagship research scenario, see verified citations and audit trail", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("admin@aiecc.local");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Engineering Overview" })).toBeVisible();

  await page.getByRole("button", { name: /Run flagship research scenario/ }).click();
  await expect(page.getByText("Citation verification")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/verified/).first()).toBeVisible();
  await expect(page.getByText("MOCK").first()).toBeVisible();

  await page.getByRole("link", { name: "Security & Audit" }).click();
  await expect(page.getByText("agent.execute").first()).toBeVisible();
  await page.getByRole("button", { name: /Verify chain integrity/ }).click();
  await expect(page.getByText(/Chain valid/)).toBeVisible();
});

test("RBAC: read-only user cannot create agents", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("viewer@aiecc.local");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("link", { name: "Agents" }).click();
  await expect(page.getByRole("heading", { name: "Agent Management Studio" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New agent" })).toHaveCount(0);
});
