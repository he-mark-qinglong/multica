import { chromium } from 'playwright';
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto('http://192.168.0.105:3210/compare', { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(4000);
  const chart = await page.$('[data-testid="multi-symbol-chart"]');
  if (!chart) { console.log('no chart'); await browser.close(); process.exit(1); }
  const box = await chart.boundingBox();
  console.log('chart box', box);
  const panels = [
    { name: 'top', y: box.y + box.height * 0.18 },
    { name: 'middle', y: box.y + box.height * 0.45 },
    { name: 'bottom', y: box.y + box.height * 0.72 },
  ];
  for (const p of panels) {
    await page.mouse.move(box.x + box.width / 2, p.y);
    await page.waitForTimeout(800);
    const hovertext = await page.$('.hovertext');
    const text = hovertext ? await hovertext.textContent() : '';
    console.log(p.name, 'hovertext', !!hovertext, text.slice(0,80));
  }
  // evaluate internals
  const info = await page.evaluate(() => {
    const gd = document.querySelector('.js-plotly-plot');
    if (!gd) return { gd: false };
    const Plotly = window.Plotly;
    const fullLayout = gd._fullLayout;
    const keys = Object.keys(fullLayout).filter(k => /^xaxis/.test(k) || /^yaxis/.test(k));
    const em = gd._em ? 'yes' : 'no';
    // try manual Fx.hover for first candle trace point 10
    const data = gd.data;
    const cand = data.findIndex(d => d.type === 'candlestick');
    let manual = '';
    if (Plotly && cand !== -1) {
      try {
        Plotly.Fx.hover(gd, [{ curveNumber: cand, pointNumber: 10 }]);
        manual = 'called';
      } catch (e) { manual = e.message; }
    }
    return { gd: true, layoutKeys: keys, em, cand, manual, dataLen: data.length };
  });
  console.log('eval', info);
  await page.screenshot({ path: '/tmp/debug_hover.png', fullPage: false });
  await browser.close();
})();
