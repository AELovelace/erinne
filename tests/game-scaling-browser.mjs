// Compare the actual embedded games at TV resolutions; inspect CSS geometry, not screenshots alone.
import assert from "node:assert/strict";

export async function checkGameScaling(page, desktop, base) { // Use one paired TV and one unpaired account to exercise the intended boundary.
  await page.bringToFront(); // Rasterize the TV tab's animated layers while inspecting its visuals.
  const screen = await page.evaluate(async () => (await fetch("/api/media/screens", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "Scaling test TV" }),
  })).json());
  await page.setViewport({ width: 1920, height: 1080 });
  await page.goto(base + "/media/screen#games");
  await page.waitForFunction(() => document.querySelectorAll(".mc-card").length === 8);

  async function measure(frame) { // Capture the physical size of text, room limits and each game's board/control.
    return frame.evaluate(() => {
      const root = document.documentElement, main = document.getElementById("gm"), slug = main.dataset.game;
      const board = document.querySelector(slug === "prism-drop" ? "#pd-field" : slug === "lumi-pet" ? ".lp-room" : ".casino-board");
      const button = document.querySelector(slug === "prism-drop" ? "#pd-drop-button" : slug === "lumi-pet" ? "[data-lp-row]" : "#casino-bet");
      const box = board.getBoundingClientRect(), buttonBox = button.getBoundingClientRect();
      return { font: parseFloat(getComputedStyle(root).fontSize), width: main.getBoundingClientRect().width,
        boardWidth: box.width, boardHeight: box.height, buttonHeight: buttonBox.height,
        buttonFont: parseFloat(getComputedStyle(button).fontSize), overflow: root.scrollWidth > innerWidth + 1,
        toolbar: !!document.querySelector(".tv-strip"), padding: getComputedStyle(document.body).padding,
        canvas: slug === "prism-drop" ? { pixels: board.width, css: box.width, dpr: devicePixelRatio } : null };
    });
  }
  for (const [slug, name] of [["prism-drop", "Prism Drop"], ["lumi-pet", "Lumi Pet"], ["blackjack", "Blackjack"], ["roulette", "Roulette"], ["horse-racing", "Horse Racing"], ["slots", "Slot Machines"]]) {
    await page.setViewport({ width: 1920, height: 1080 });
    await page.evaluate(title => [...document.querySelectorAll(".mc-card")].find(b => b.querySelector(".mc-label").textContent === title).click(), name);
    const frame = await (await page.waitForSelector("#mc-game-frame")).contentFrame();
    await frame.waitForFunction(game => document.getElementById("gm")?.dataset.game === game, {}, slug);
    if (!["prism-drop", "lumi-pet"].includes(slug)) {
      await frame.waitForSelector("#casino-create:not([disabled])");
      await frame.click("#casino-create");
      await frame.waitForFunction(() => !document.getElementById("casino-table").hidden);
    }
    if (slug === "lumi-pet") await frame.waitForSelector("[data-lp-row]"); // Her list arrives with her state from the server.
    const normal = await measure(frame);
    assert.equal(normal.font, 16, slug + " keeps 1080p sizing");
    assert.equal(normal.toolbar, false); assert.equal(normal.padding, "0px"); assert.equal(normal.overflow, false);
    if (slug === "slots" || slug === "lumi-pet") await page.screenshot({ path: `runs/casino-browser/${slug}-1080p.png`, fullPage: true });
    await page.setViewport({ width: 3840, height: 2160 });
    await frame.waitForFunction(() => parseFloat(getComputedStyle(document.documentElement).fontSize) === 32);
    const large = await measure(frame);
    assert.equal(large.overflow, false, slug + " fits 4K horizontally");
    for (const key of ["width", "boardWidth", "boardHeight", "buttonHeight", "buttonFont"]) {
      const ratio = large[key] / normal[key];
      assert.ok(ratio >= 1.85 && ratio <= 2.15, `${slug} ${key} must grow with 4K UI scale: ${ratio}`);
    }
    if (large.canvas) assert.ok(Math.abs(large.canvas.pixels - large.canvas.css * large.canvas.dpr) < 5, "Prism canvas stays sharp");
    if (slug === "slots" || slug === "lumi-pet") {
      await new Promise(resolve => setTimeout(resolve, 1000)); // Allow Chrome to rasterize the resized 4K layers before capturing them.
      await page.screenshot({ path: `runs/casino-browser/${slug}-4k.png`, fullPage: true });
    }
    await page.setViewport({ width: 2560, height: 1440 });
    await frame.waitForFunction(() => parseFloat(getComputedStyle(document.documentElement).fontSize) === 24);
    await page.setViewport({ width: 1920, height: 1080 });
    await frame.waitForFunction(() => parseFloat(getComputedStyle(document.documentElement).fontSize) === 16);
    await page.click("#mc-game-back");
    console.log(`${slug}: 1080p 1x, 1440p 1.5x, 4K 2x and resize-back geometry passed`);
  }

  await page.setViewport({ width: 3840, height: 2160 });
  await page.evaluate(async () => { // Load an explicit saved scale instead of Automatic.
    const response = await fetch("/media/screen/ui-scale", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scale: 1.5 }) });
    if (!response.ok) throw new Error("Could not save TV scale");
  });
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll(".mc-card").length === 8);
  await page.evaluate(() => [...document.querySelectorAll(".mc-card")].find(b => b.querySelector(".mc-label").textContent === "Slot Machines").click());
  const frame = await (await page.waitForSelector("#mc-game-frame")).contentFrame();
  await frame.waitForFunction(() => document.getElementById("casino-table") && !document.getElementById("casino-table").hidden);
  assert.equal((await measure(frame)).font, 24);
  const code = await frame.evaluate(() => sessionStorage.getItem("casino-room:slots"));

  async function sizeCommand(step) { // Drive the existing Text size controls over the actual TV command stream.
    await page.evaluate(async (id, value) => {
      const response = await fetch("/api/media/screens/" + id + "/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "text_size", step: value }) });
      if (!response.ok) throw new Error("Text size command failed");
    }, screen.id, step);
  }
  await sizeCommand("open");
  await page.waitForSelector("#mc-text", { visible: true });
  await sizeCommand("bigger");
  await frame.waitForFunction(() => parseFloat(getComputedStyle(document.documentElement).fontSize) === 28);
  await sizeCommand("auto");
  await frame.waitForFunction(() => parseFloat(getComputedStyle(document.documentElement).fontSize) === 32);
  await sizeCommand("cancel");
  await frame.waitForFunction(() => parseFloat(getComputedStyle(document.documentElement).fontSize) === 24);
  assert.equal(await frame.evaluate(() => sessionStorage.getItem("casino-room:slots")), code, "Scale changes preserve the table");
  await page.setViewport({ width: 1920, height: 1080 });
  assert.equal((await measure(frame)).font, 24, "Saved custom scale overrides automatic width rules");

  await desktop.bringToFront();
  await desktop.setViewport({ width: 3840, height: 2160 });
  await desktop.goto(base + "/media/games/slots");
  assert.equal(await desktop.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize)), 16, "Unpaired desktops keep their existing scale");
  console.log("Saved TV text size, live Bigger/Automatic/Cancel, table preservation and unpaired desktop sizing passed");
}
