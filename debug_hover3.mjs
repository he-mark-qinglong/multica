import { chromium } from 'playwright';
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto('http://192.168.0.105:3210/compare', { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(4000);
  const chart = await page.$('[data-testid="multi-symbol-chart"]');
  const box = await chart.boundingBox();
  // intercept Plotly.Fx.hover
  await page.evaluate(() => {
    const Plotly = window.Plotly;
    const orig = Plotly.Fx.hover;
    window.hoverCalls = [];
    Plotly.Fx.hover = function(gd, arg) { window.hoverCalls.push({arg: JSON.stringify(arg).slice(0,200), time: Date.now()}); return orig.apply(this, arguments); };
  });
  const panels = [
    { name: 'top', y: box.y + box.height * 0.18 },
    { name: 'middle', y: box.y + box.height * 0.45 },
    { name: 'bottom', y: box.y + box.height * 0.72 },
  ];
  for (const p of panels) {
    await page.mouse.move(box.x + box.width / 2, p.y);
    await page.waitForTimeout(800);
    const calls = await page.evaluate(() => { const c = window.hoverCalls; window.hoverCalls = []; return c; });
    const hovertext = await page.$('.hovertext');
    const text = hovertext ? await hovertext.textContent() : '';
    console.log(p.name, 'calls', calls.length, 'text', text.slice(0,80));
  }
  await browser.close();
})();
