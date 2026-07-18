import { chromium } from 'playwright';
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto('http://192.168.0.105:3210/compare', { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(4000);
  const chart = await page.$('[data-testid="multi-symbol-chart"]');
  const box = await chart.boundingBox();
  const Plotly = await page.evaluateHandle(() => window.Plotly);
  const panels = [
    { name: 'top', y: box.y + box.height * 0.18 },
    { name: 'middle', y: box.y + box.height * 0.45 },
    { name: 'bottom', y: box.y + box.height * 0.72 },
  ];
  for (const p of panels) {
    const result = await page.evaluate(({clientX, clientY}) => {
      const gd = document.querySelector('.js-plotly-plot');
      const Plotly = window.Plotly;
      const fullLayout = gd._fullLayout;
      const rect = gd.getBoundingClientRect();
      const margin = fullLayout.margin;
      const mouseX = clientX - rect.left;
      const mouseY = clientY - rect.top;
      const plotHeight = rect.height - margin.t - margin.b;
      const paperY = 1 - (mouseY - margin.t) / plotHeight;
      const axisKeys = ['yaxis','yaxis2','yaxis3'];
      let panelIndex = null;
      for (let i=0;i<axisKeys.length;i++){ const domain=fullLayout[axisKeys[i]].domain; if (paperY>=domain[0] && paperY<=domain[1]) { panelIndex=i; break; } }
      if (panelIndex===null) return {paperY, panelIndex, text:''};
      const xaxisName = panelIndex===0?'xaxis':`xaxis${panelIndex+1}`;
      const xaxis = fullLayout[xaxisName];
      const targetDate = xaxis.p2d(mouseX);
      const targetMs = Date.parse(targetDate);
      const cand = gd.data.findIndex(d => d.type==='candlestick' && (d.xaxis||'x')===(panelIndex===0?'x':`x${panelIndex+1}`));
      if (cand===-1) return {paperY, panelIndex, xaxisName, targetDate, cand, text:''};
      // nearest index
      const trace = gd.data[cand];
      let best=0, bestDist=Infinity;
      for (let i=0;i<trace.x.length;i++){ const d=Math.abs(Date.parse(trace.x[i])-targetMs); if(d<bestDist){bestDist=d;best=i;} }
      Plotly.Fx.unhover(gd);
      Plotly.Fx.hover(gd, [{curveNumber:cand, pointNumber:best}]);
      const ht = document.querySelector('.hovertext');
      return {paperY, panelIndex, xaxisName, targetDate, cand, best, text: ht?ht.textContent:''};
    }, {clientX: box.x + box.width/2, clientY: p.y});
    console.log(p.name, result.text.slice(0,120).replace(/\n/g,' '));
  }
  await browser.close();
})();
