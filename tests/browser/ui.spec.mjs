import assert from "node:assert/strict";
import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
let queryMode = "success";
let decisionSaved = false;

await page.route("**/health", (route) =>
  route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) })
);
await page.route("**/ready", (route) =>
  route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ready" }) })
);
await page.route("**/v1/history", (route) =>
  route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) })
);
await page.route("**/v1/traces", (route) =>
  route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      items: decisionSaved
        ? [{ trace_id: "trace-1", action: "Review sales", confidence: "medium" }]
        : [],
    }),
  })
);
await page.route("**/v1/query", async (route) => {
  const failures = {
    timeout: { status: 504, error: "query_timeout" },
    rejected: { status: 422, error: "request_rejected_by_validation_or_policy" },
    execution: { status: 422, error: "query_execution_failed" },
    busy: { status: 503, error: "server_busy" },
    failed: { status: 500, error: "request_failed" },
  };
  if (failures[queryMode]) {
    const failure = failures[queryMode];
    await route.fulfill({
      status: failure.status,
      contentType: "application/json",
      body: JSON.stringify({ error: failure.error }),
    });
    return;
  }
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      question: "count rows in sales",
      columns: ["row_count"],
      rows: [[4]],
      truncated: false,
      sql: 'SELECT COUNT(*) AS "row_count" FROM "sales"',
      parameter_count: 0,
      evidence_id: "evidence-1",
      evidence_sequence: 1,
      schema_fingerprint: "schema-1",
      planner_reference: "rules-v1",
      prompt_sha256: null,
    }),
  });
});
await page.route("**/v1/decision", async (route) => {
  decisionSaved = true;
  await route.fulfill({
    status: 201,
    contentType: "application/json",
    body: JSON.stringify({
      proposal_origin: "caller_supplied",
      sequence: 1,
      trace: { trace_id: "trace-1", decision: { action: "Review sales" } },
    }),
  });
});

try {
  await page.goto("http://127.0.0.1:4173");
  await page.getByText("API ready").waitFor();
  assert.equal(await page.locator("html").getAttribute("lang"), "en");
  assert.equal(await page.locator("html").getAttribute("dir"), "ltr");

  await page.getByRole("button", { name: "Switch to Persian" }).click();
  assert.equal(await page.locator("html").getAttribute("lang"), "fa");
  assert.equal(await page.locator("html").getAttribute("dir"), "rtl");
  assert.equal(await page.locator("h1").textContent(), "دستیار تبدیل داده به تصمیم");
  assert.equal(await page.locator("#question").getAttribute("placeholder"), "سؤال خود را به فارسی یا انگلیسی بنویسید…");

  await page.getByRole("button", { name: "تغییر زبان به انگلیسی" }).click();
  assert.equal(await page.locator("html").getAttribute("dir"), "ltr");
  await page.locator("#question").fill("count rows in sales");
  await page.getByRole("button", { name: "Run query" }).click();
  await page.getByText("Query completed with persisted evidence.").waitFor();
  assert.equal(await page.locator("#result tbody td").textContent(), "4");
  await page.locator("#decision-action").fill("Review sales");
  await page.locator("#decision-rationale").fill("Four rows were returned.");
  await page.locator("#decision-claim").fill("The query returned four rows.");
  await page.locator("#decision-alternative").fill("Keep current plan");
  await page.locator("#decision-tradeoff").fill("May delay action.");
  await page.locator("#decision-reversal").fill("Reassess after a weekly trend query.");
  await page.getByRole("button", { name: "Verify and save decision" }).click();
  await page.getByText(/Decision verified and saved/).waitFor();
  await page.getByRole("button", { name: /Review sales · medium · trace-1/ }).waitFor();

  queryMode = "timeout";
  await page.locator("#question").fill("count rows in sales");
  await page.getByRole("button", { name: "Run query" }).click();
  await page.getByText("Query timed out. Reduce the query scope or retry later.").waitFor();

  await page.getByRole("button", { name: "Switch to Persian" }).click();
  assert.equal(await page.locator("html").getAttribute("dir"), "rtl");
  await page.getByText("زمان اجرای پرس‌وجو تمام شد. دامنه پرس‌وجو را محدود کنید یا بعداً دوباره تلاش کنید.").waitFor();

  queryMode = "rejected";
  await page.locator("#question").fill("unsupported request");
  await page.getByRole("button", { name: "اجرای پرس‌وجو" }).click();
  await page.getByText("پرس‌وجو توسط اعتبارسنجی یا سیاست ایمنی رد شد. سؤال را بررسی کنید و درخواست پشتیبانی‌شده‌ای بفرستید.").waitFor();

  await page.getByRole("button", { name: "تغییر زبان به انگلیسی" }).click();
  queryMode = "execution";
  await page.locator("#question").fill("count rows in sales");
  await page.getByRole("button", { name: "Run query" }).click();
  await page.getByText("The query could not be executed against the catalog. Check the query scope and catalog state.").waitFor();

  queryMode = "busy";
  await page.getByRole("button", { name: "Run query" }).click();
  await page.getByText("The service is handling its current workload. Wait briefly and retry.").waitFor();

  queryMode = "failed";
  await page.getByRole("button", { name: "Run query" }).click();
  await page.getByText("The service could not complete the request. Check service health and operator logs.").waitFor();
  console.log("Browser UI checks passed: bilingual RTL, query, decision, timeout, rejection, execution, busy, and generic failure states.");
} finally {
  await browser.close();
}
