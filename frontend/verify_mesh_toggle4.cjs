const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const errors = [];
  page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));

  await page.goto("http://localhost:5173/mesh", { waitUntil: "load" });
  await page.waitForSelector(".btn-view-toggle", { timeout: 10000 });

  const parentTag2D = await page.evaluate(() => document.querySelector(".btn-view-toggle").parentElement.tagName);
  console.log("2D toggle's parent element:", parentTag2D);

  await page.click(".btn-view-toggle");
  await page.waitForSelector(".btn-view-toggle", { timeout: 15000 });
  await page.waitForTimeout(1500);

  const parentTag3D = await page.evaluate(() => document.querySelector(".btn-view-toggle").parentElement.tagName);
  console.log("3D toggle's parent element:", parentTag3D);

  await page.screenshot({ path: "/private/tmp/claude-501/-Users-danielbenitez-MeshBoard/c53cd9a0-8fe9-473f-a6fb-f0b6716d1ceb/scratchpad/v4-3d.png" });

  console.log("console/page errors:", JSON.stringify(errors, null, 2));

  await browser.close();
})();
