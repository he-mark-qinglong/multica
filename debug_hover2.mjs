import { chromium } from 'playwright';
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto('http://192.168.0.105:3210/compare', { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(4000);
  const info = await page.evaluate(() => {
    const gd = document.querySelector('.js-plotly-plot');
    const Plotly = window.Plotly;
    const out = {};
    out.dataTypes = gd.data.map(d => ({type:d.type, xaxis:d.xaxis, yaxis:d.yaxis, len: d.x ? d.x.length : 0}));
    out.hoverLayer = !!gd.querySelector('.hoverlayer');
    // Call hover on each candle trace point 5
    gd.data.forEach((d, i) => {
      if (d.type === 'candlestick') {
        try { Plotly.Fx.hover(gd, [{ curveNumber: i, pointNumber: 5 }]); out['hover_'+i] = 'called'; } catch(e){ out['hover_'+i] = e.message; }
      }
    });
    // check DOM
    out.hovertextCount = document.querySelectorAll('.hovertext').length;
    out.hoverlayerChildren = Array.from(document.querySelectorAll('.hoverlayer > *')).map(el => el.className.baseVal || el.tagName);
    out.gdClasses = gd.className;
    return out;
  });
  console.log(info);
  await page.screenshot({ path: '/tmp/debug_hover2.png', fullPage: false });
  await browser.close();
})();
