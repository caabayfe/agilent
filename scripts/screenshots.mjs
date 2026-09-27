// Regenerates docs/img/*.png from the running stack (gateway on `live`, after `make eval` and `make eval-matrix`).
// Uses the installed Google Chrome; playwright-core is not a project dependency:
//   mkdir -p /tmp/shots && cd /tmp/shots && npm i playwright-core@1 && cp <repo>/scripts/screenshots.mjs .
//   node screenshots.mjs <repo>/docs/img
// It asks one live question (E-217) and opens the model selector without applying anything.
import { chromium } from "playwright-core";

const OUT = process.argv[2];
const BASE = "http://localhost:5173";
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 1,
});

async function home() {
  await page.goto(BASE + "/");
  await page.getByPlaceholder("Ask about an order").waitFor();
  await page.waitForLoadState("networkidle");
}

// 1. Chat + trace: troubleshooting question as Alice (routes to assistant-reasoning).
await home();
await page
  .getByPlaceholder("Ask about an order")
  .fill("My LC-900 shows error E-217 during a run. What should I do?");
await page.getByRole("button", { name: "Send" }).click();
await page
  .getByText("answered by", { exact: false })
  .first()
  .waitFor({ timeout: 90_000 });
await page.getByRole("tab", { name: "Trace & audit" }).click();
await page.waitForTimeout(1500);
await page.screenshot({ path: `${OUT}/chat-trace.png` });

// 2. Skills tab.
await page.getByRole("tab", { name: "Skills" }).click();
await page.waitForTimeout(1500);
await page.screenshot({ path: `${OUT}/skills.png` });

// 3. Evals tab.
await page.getByRole("tab", { name: "Evals & promotion" }).click();
await page.waitForTimeout(2000);
await page.screenshot({ path: `${OUT}/evals.png` });

// 4. Model selector: open it and pick a model, but do not apply.
await page.getByRole("button", { name: "Change models ▾" }).click();
const selector = page
  .getByRole("dialog", { name: "Model selector" })
  .or(page.getByLabel("Model selector"));
await selector.first().waitFor();
await page.waitForTimeout(1000);
const fast = selector.first().locator("select").first();
await fast
  .selectOption({ label: "gpt-4o" })
  .catch(() => fast.selectOption("azure-gpt-4o"));
await page.waitForTimeout(500);
await page.screenshot({ path: `${OUT}/model-selector.png` });

// 5. Model matrix (tall viewport) and the evidence behind one failing cell.
await page.getByRole("button", { name: "Close" }).click();
await page.setViewportSize({ width: 1440, height: 1500 });
await page.getByRole("button", { name: "Model matrix" }).click();
await page.getByText("Gates per combination").waitFor();
await page.waitForTimeout(1500);
await page.screenshot({ path: `${OUT}/model-matrix.png` });
await page.setViewportSize({ width: 1440, height: 1000 });
const failing = page.locator("button[aria-label]").filter({ hasText: "✗" });
if (await failing.count()) {
  await failing.last().click();
  await page
    .getByRole("region", { name: "Case evidence" })
    .scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/model-matrix-evidence.png` });
}

await browser.close();
console.log("ok");
