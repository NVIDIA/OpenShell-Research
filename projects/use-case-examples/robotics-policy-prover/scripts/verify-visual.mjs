import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

const url = process.env.DEMO_URL ?? "http://127.0.0.1:5173";
const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
];

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

await mkdir("screenshots", { recursive: true });

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH || undefined,
});
try {
  for (const viewport of viewports) {
    const page = await browser.newPage({ viewport });
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("canvas");
    await page.waitForTimeout(1400);

    const stats = await page.evaluate(() => {
      const canvas = document.querySelector("canvas");
      const sample = document.createElement("canvas");
      sample.width = 120;
      sample.height = 80;
      const context = sample.getContext("2d", { willReadFrequently: true });
      context.drawImage(canvas, 0, 0, sample.width, sample.height);
      const image = context.getImageData(0, 0, sample.width, sample.height).data;
      let brightPixels = 0;
      let darkPixels = 0;
      let colorSpread = 0;

      for (let index = 0; index < image.length; index += 4) {
        const red = image[index];
        const green = image[index + 1];
        const blue = image[index + 2];
        const luma = red * 0.2126 + green * 0.7152 + blue * 0.0722;
        if (luma > 180) brightPixels += 1;
        if (luma < 110) darkPixels += 1;
        if (Math.abs(red - green) + Math.abs(green - blue) > 35) colorSpread += 1;
      }

      return {
        width: canvas.width,
        height: canvas.height,
        brightPixels,
        darkPixels,
        colorSpread,
      };
    });

    assert(stats.width > 0 && stats.height > 0, `${viewport.name}: canvas has no size`);
    assert(stats.brightPixels > 150, `${viewport.name}: canvas lacks lit geometry`);
    assert(stats.darkPixels > 20, `${viewport.name}: canvas lacks shaded geometry`);
    assert(stats.colorSpread > 60, `${viewport.name}: canvas lacks colored policy elements`);

    await page.screenshot({
      path: `screenshots/${viewport.name}.png`,
      fullPage: true,
    });
    await page.close();
  }
} finally {
  await browser.close();
}

console.log(`Visual verification passed for ${viewports.length} viewports at ${url}`);
