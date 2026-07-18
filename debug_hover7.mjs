import { chromium } from 'playwright';
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto('http://192.168.0.105:3210/compare', { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(4000);
  const out = await page.evaluate(() => {
    const gd = document.querySelector('.js-plotly-plot');
    const Plotly = window.Plotly;
    const orig = Plotly.Fx.hover.toString().slice(0,500);
    const results = [];
    // Try various calls for middle panel trace 1
    const cand = 1;
    const tests = [
      () => Plotly.Fx.hover(gd, [{curveNumber:cand, pointNumber:10}], 'x2y2'),
      () => Plotly.Fx.hover(gd, [{xval: gd.data[cand].x[10], yval: gd.data[cand].close[10], xaxis: 'x2', yaxis: 'y2'}], 'x2y2'),
      () => Plotly.Fx.hover(gd, [{xval: gd.data[cand].x[10], yval: gd.data[cand].close[10]}], 'x2y2'),
      () => Plotly.Fx.hover(gd, [{curveNumber:cand, pointNumber:10, xaxis: 'x2', yaxis: 'y2'}]),
    ];
    tests.forEach((fn,i)=>{ try{ fn(); results.push({i, ok:true, text: (document.querySelector('.hovertext')?.textContent||'').slice(0,80)}); Plotly.Fx.unhover(gd); } catch(e){ results.push({i, ok:false, err:e.message}); } });
    return { orig, results };
  });
  console.log(out.orig);
  console.log(JSON.stringify(out.results, null, 2));
  await browser.close();
})();
