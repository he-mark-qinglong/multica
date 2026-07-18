import { chromium } from 'playwright';
(async () => {
  for (const panelName of ['top','middle','bottom']) {
    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.goto('http://192.168.0.105:3210/compare', { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(4000);
    const chart = await page.$('[data-testid="multi-symbol-chart"]');
    const box = await chart.boundingBox();
    const ratios = {top:0.18, middle:0.45, bottom:0.72};
    const clientY = box.y + box.height * ratios[panelName];
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
      const trace = gd.data[cand];
      let best=0, bestDist=Infinity;
      for (let i=0;i<trace.x.length;i++){ const d=Math.abs(Date.parse(trace.x[i])-targetMs); if(d<bestDist){bestDist=d;best=i;} }
      try {
        Plotly.Fx.hover(gd, [{curveNumber:cand, pointNumber:best}]);
      } catch(e) { return {error:e.message}; }
      const ht = document.querySelector('.hovertext');
      return {paperY, panelIndex, xaxisName, targetDate, cand, best, text: ht?ht.textContent.slice(0,120):''};
    }, {clientX: box.x + box.width/2, clientY: clientY});
    console.log(panelName, JSON.stringify(result));
    await browser.close();
  }
})();
