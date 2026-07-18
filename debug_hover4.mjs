import { chromium } from 'playwright';
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto('http://192.168.0.105:3210/compare', { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(4000);
  const chart = await page.$('[data-testid="multi-symbol-chart"]');
  const box = await chart.boundingBox();
  const panels = [
    { name: 'top', y: box.y + box.height * 0.18 },
    { name: 'middle', y: box.y + box.height * 0.45 },
    { name: 'bottom', y: box.y + box.height * 0.72 },
  ];
  for (const p of panels) {
    await page.mouse.move(box.x + box.width / 2, p.y);
    await page.waitForTimeout(500);
    const diag = await page.evaluate((clientY) => {
      const gd = document.querySelector('.js-plotly-plot');
      const fullLayout = gd._fullLayout;
      const rect = gd.getBoundingClientRect();
      const margin = fullLayout.margin;
      const mouseY = clientY - rect.top;
      const plotHeight = rect.height - margin.t - margin.b;
      const normY = (mouseY - margin.t) / plotHeight;
      const panelCount = (fullLayout.yaxis ? 1 : 0) + Object.keys(fullLayout).filter(k => /^yaxis[2-9]/.test(k)).length;
      let panelIndex = null;
      for (let i=0;i<panelCount;i++) { const key=i===0?'yaxis':`yaxis${i+1}`; const domain=fullLayout[key]?.domain; if (domain && normY>=domain[0] && normY<=domain[1]) { panelIndex=i; break; } }
      let result = { rect, margin, mouseY, plotHeight, normY, panelCount, panelIndex };
      if (panelIndex !== null) {
        const xaxisName = panelIndex===0?'xaxis':`xaxis${panelIndex+1}`;
        const xaxis = fullLayout[xaxisName];
        const mouseX = rect.width/2;
        const targetDate = xaxis.p2d(mouseX);
        result = { ...result, xaxisName, targetDate, xaxisRange: xaxis.range, domain: fullLayout[panelIndex===0?'yaxis':`yaxis${panelIndex+1}`].domain };
      }
      return result;
    }, p.y);
    console.log(p.name, JSON.stringify(diag));
  }
  await browser.close();
})();
