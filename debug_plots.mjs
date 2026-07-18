import { chromium } from 'playwright';
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto('http://192.168.0.105:3210/compare', { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(4000);
  const out = await page.evaluate(() => {
    const gd = document.querySelector('.js-plotly-plot');
    const fl = gd._fullLayout;
    return {
      plots: Object.keys(fl._plots || {}),
      xaxis2: { domain: fl.xaxis2.domain, anchor: fl.xaxis2.anchor, range: fl.xaxis2.range, _id: fl.xaxis2._id, _subplots: fl.xaxis2._subplots },
      xaxis3: { domain: fl.xaxis3.domain, anchor: fl.xaxis3.anchor, range: fl.xaxis3.range },
      yaxis2: { domain: fl.yaxis2.domain, anchor: fl.yaxis2.anchor },
      yaxis3: { domain: fl.yaxis3.domain, anchor: fl.yaxis3.anchor },
    };
  });
  console.log(out);
  await browser.close();
})();
