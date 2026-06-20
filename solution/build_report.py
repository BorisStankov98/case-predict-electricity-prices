#!/usr/bin/env python
"""Turn outputs/report_data.json into a self-contained interactive report.html.

    python -m solution.build_report          # after run_pipeline has produced data
    open solution/outputs/report.html        # macOS

The data is inlined into the HTML, so the file works offline via file:// (Plotly
itself is pulled from a CDN; an internet connection is needed only for the chart
library, not the data).
"""
from __future__ import annotations

import json
import os
import sys
import webbrowser
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution.src import config as C

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Bulgarian Electricity Forecasting — Results</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>
  :root{ --bg:#0f1320; --card:#1a2032; --ink:#e8ecf6; --muted:#9aa6c4;
         --good:#3ddc97; --bad:#ff6b6b; --accent:#5b8cff; --line:#2a3350; }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  header{padding:32px 40px 18px;border-bottom:1px solid var(--line)}
  h1{margin:0 0 6px;font-size:27px;letter-spacing:.2px}
  .sub{color:var(--muted);font-size:14px}
  main{padding:24px 40px 60px;max-width:1200px;margin:0 auto}
  h2{font-size:19px;margin:40px 0 6px;border-left:3px solid var(--accent);padding-left:10px}
  .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(205px,1fr));gap:14px;margin-top:14px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
  .card .k{color:var(--muted);font-size:12.5px;text-transform:uppercase;letter-spacing:.6px}
  .card .v{font-size:25px;font-weight:700;margin-top:4px}
  .card .d{font-size:12.5px;color:var(--muted);margin-top:2px}
  .pos{color:var(--good)} .neg{color:var(--bad)}
  table{border-collapse:collapse;width:100%;font-size:13.5px;background:var(--card);
        border-radius:12px;overflow:hidden}
  th,td{padding:9px 12px;text-align:right;border-bottom:1px solid var(--line)}
  th:first-child,td:first-child{text-align:left}
  thead th{background:#212a42;color:var(--muted);font-weight:600;
           text-transform:uppercase;font-size:11.5px;letter-spacing:.5px}
  tr.model td{font-weight:600}
  tr.win td{background:rgba(61,220,151,.08)}
  .best{color:var(--good);font-weight:700}
  .controls{display:flex;gap:10px;align-items:center;margin:14px 0 0;flex-wrap:wrap}
  select{background:var(--card);color:var(--ink);border:1px solid var(--line);
         border-radius:8px;padding:8px 11px;font-size:14px}
  .hint{color:var(--muted);font-size:12.5px}
  .plot{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:10px;margin-top:12px}
  .note{color:var(--muted);font-size:13px;margin-top:10px}
  footer{color:var(--muted);font-size:12.5px;padding:24px 40px;border-top:1px solid var(--line)}
  code{background:#0c1020;border:1px solid var(--line);border-radius:5px;padding:1px 5px}
</style>
</head>
<body>
<header>
  <h1>⚡ Bulgarian Electricity Forecasting — Results</h1>
  <div class="sub">Consumption · Supply · Price &nbsp;|&nbsp; horizons: 15&nbsp;min, 24&nbsp;h, 1&nbsp;week
    &nbsp;|&nbsp; held-out test set &nbsp;|&nbsp; generated __GENERATED__</div>
  <div class="sub">Dataset: __ROWS__ hourly rows × __COLS__ cols, __START__ → __END__</div>
</header>
<main>
  <h2>Skill vs. persistence</h2>
  <div class="cards" id="cards"></div>
  <div class="note">Skill = 1 − MAE<sub>model</sub> / MAE<sub>persistence</sub>.
    Positive (green) = the model beats the naive “same value h ago” baseline.</div>

  <h2>Forecast vs. actual</h2>
  <div class="controls">
    <select id="sel"></select>
    <span class="hint">Use the buttons or the slider below the chart to zoom / pan. Drag to select a window.</span>
  </div>
  <div class="plot"><div id="ts" style="height:480px"></div></div>

  <h2>Skill by layer &amp; horizon</h2>
  <div class="plot"><div id="bars" style="height:380px"></div></div>

  <h2>What drives each target — correlation heatmap</h2>
  <div class="plot"><div id="heat" style="height:720px"></div></div>
  <div class="note">Pearson correlation of each driver with the three targets (contemporaneous).
    Numbers shown in-cell; blue = positive, red = negative. E.g. solar vs. price is
    strongly negative (merit-order effect); temperature vs. load is negative (winter heating).</div>

  <h2>Full metrics</h2>
  <div id="tablewrap"></div>
</main>
<footer>
  Built by <code>solution/build_report.py</code> from <code>outputs/report_data.json</code>.
  Charts: Plotly. &nbsp;Reproduce: <code>python -m solution.run_pipeline &amp;&amp; python -m solution.build_report</code>.
</footer>

<script>
const DATA = __DATA__;
const PLOT_BG = {paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
  font:{color:'#cdd6f0',size:13}, margin:{l:60,r:24,t:20,b:40}};
const PCONF = {displayModeBar:false, responsive:true};
const pretty = {L1_load:"Load",L2_gen:"Generation",L3_price:"Price"};
const order = ["L3_price_15min","L3_price_24h","L3_price_1week",
               "L2_gen_24h","L2_gen_1week","L1_load_24h","L1_load_1week"];

// ---------- skill cards ----------
const cardEl = document.getElementById('cards');
DATA.skill.sort((a,b)=>order.indexOf(a.layer+"_"+a.horizon)-order.indexOf(b.layer+"_"+b.horizon));
for(const s of DATA.skill){
  const pct=s.skill_vs_persistence*100;
  const cls=pct>=0?'pos':'neg', sign=pct>=0?'+':'';
  const a=(s.alpha==null)?'–':s.alpha.toFixed(1);
  cardEl.insertAdjacentHTML('beforeend',
   `<div class="card"><div class="k">${pretty[s.layer]} · ${s.horizon}</div>
    <div class="v ${cls}">${sign}${pct.toFixed(1)}%</div>
    <div class="d">α=${a} · vs persistence</div></div>`);
}

// ---------- time-series ----------
const sel=document.getElementById('sel');
for(const k of order){ if(DATA.series[k]){
  const o=document.createElement('option'); o.value=k;
  o.textContent=`${pretty[k.split('_').slice(0,2).join('_')]} — ${k.split('_').slice(2).join('_')}`;
  sel.appendChild(o);
}}
function unit(k){return k.startsWith('L3_price')?'EUR/MWh':'MW';}
function drawTS(k){
  const d=DATA.series[k], t=d.t;
  // High-contrast, color-blind-friendly trio: cyan (dashed) / white / orange.
  const traces=[
    {x:t,y:d.persistence,name:'persistence',mode:'lines',
       line:{width:1.6,color:'#22d3ee',dash:'dot'}},
    {x:t,y:d.actual,name:'actual',mode:'lines',line:{width:2.8,color:'#f4f7ff'}},
    {x:t,y:d.model,name:'model (forecast)',mode:'lines',line:{width:2.2,color:'#ff8c42'}},
  ];
  // Default to a readable window (10 days hourly / 7 days for 15-min); slider shows the rest.
  const isQH = k.endsWith('15min');
  const N = Math.min(t.length, isQH ? 96*7 : 24*10);
  const layout=Object.assign({},PLOT_BG,{
    height:480,
    legend:{orientation:'h',y:1.13,x:0},
    yaxis:{title:unit(k),gridcolor:'#2a3350',zeroline:false},
    xaxis:{gridcolor:'#2a3350', range:[t[0],t[N-1]],
      rangeslider:{visible:true,thickness:0.09,bgcolor:'#11172a',bordercolor:'#2a3350'},
      rangeselector:{buttons:[
        {step:'day',count:2,label:'2d'},
        {step:'day',count:7,label:'1w'},
        {step:'day',count:14,label:'2w'},
        {step:'all',label:'all'}],
        bgcolor:'#1a2032',activecolor:'#5b8cff',font:{color:'#cdd6f0'},x:0,y:1.0}},
  });
  Plotly.newPlot('ts',traces,layout,PCONF);
}
sel.addEventListener('change',e=>drawTS(e.target.value));
drawTS(order.find(k=>DATA.series[k]));

// ---------- skill bar chart ----------
(function(){
  const lab=DATA.skill.map(s=>`${pretty[s.layer]}<br>${s.horizon}`);
  const val=DATA.skill.map(s=>+(s.skill_vs_persistence*100).toFixed(1));
  const col=val.map(v=>v>=0?'#3ddc97':'#ff6b6b');
  Plotly.newPlot('bars',[{type:'bar',x:lab,y:val,marker:{color:col},
    text:val.map(v=>(v>=0?'+':'')+v+'%'),textposition:'outside',
    cliponaxis:false,textfont:{size:13}}],
    Object.assign({},PLOT_BG,{margin:{l:60,r:24,t:30,b:50},
      yaxis:{title:'skill vs persistence (%)',gridcolor:'#2a3350',
        zeroline:true,zerolinecolor:'#52608a',zerolinewidth:2}}),PCONF);
})();

// ---------- correlation heatmap ----------
function pretD(s){
  const m={'load_forecast':'Load forecast (day-ahead)','gen_forecast':'Generation forecast (day-ahead)',
    'gen_solar':'Solar generation','gen_wind_onshore':'Wind generation','gen_nuclear':'Nuclear',
    'gen_fossil_brown_coal_lignite':'Lignite','gen_fossil_gas':'Gas',
    'solar_forecast':'Solar forecast','wind_forecast':'Wind forecast',
    'net_position':'Net position','outage_unavail':'Outages (unavailable MW)'};
  if(m[s])return m[s];
  if(s.startsWith('wx:'))return 'Weather · '+s.slice(3).replace(/_/g,' ');
  if(s.startsWith('net_flow_'))return 'Net flow · '+s.slice(9);
  return s.replace(/_/g,' ');
}
(function(){
  const c=DATA.correlations;
  const ylabels=c.drivers.map(pretD);
  Plotly.newPlot('heat',[{z:c.matrix,x:c.targets,y:ylabels,type:'heatmap',
    zmin:-1,zmax:1,colorscale:'RdBu',reversescale:true,xgap:3,ygap:3,
    text:c.matrix,texttemplate:'%{z}',textfont:{size:13,color:'#0c1020'},
    colorbar:{title:'r',thickness:14},
    hovertemplate:'%{y}<br>vs %{x}: <b>%{z}</b><extra></extra>'}],
    Object.assign({},PLOT_BG,{height:60+ylabels.length*30,
      margin:{l:250,r:24,t:10,b:40},
      xaxis:{side:'top',tickfont:{size:14}},yaxis:{tickfont:{size:13},autorange:'reversed'}}),PCONF);
})();

// ---------- metrics table ----------
(function(){
  const rows=DATA.metrics, bestMAE={};
  for(const r of rows){const k=r.layer+'_'+r.horizon;
    if(!(k in bestMAE)||r.MAE<bestMAE[k])bestMAE[k]=r.MAE;}
  let h='<table><thead><tr><th>Layer</th><th>Horizon</th><th>Estimator</th>'+
        '<th>n</th><th>MAE</th><th>RMSE</th><th>MAPE</th><th>sMAPE</th></tr></thead><tbody>';
  for(const r of rows){
    const k=r.layer+'_'+r.horizon, isModel=r.estimator==='model';
    const win=isModel && r.MAE<=bestMAE[k];
    const maeCls=(r.MAE===bestMAE[k])?'best':'';
    h+=`<tr class="${isModel?'model':''} ${win?'win':''}">`+
       `<td>${pretty[r.layer]||r.layer}</td><td>${r.horizon}</td><td>${r.estimator}</td>`+
       `<td>${r.n}</td><td class="${maeCls}">${r.MAE}</td><td>${r.RMSE}</td>`+
       `<td>${r.MAPE}</td><td>${r.sMAPE}</td></tr>`;
  }
  document.getElementById('tablewrap').innerHTML=h+'</tbody></table>';
})();
</script>
</body>
</html>
"""


def main():
    data_path = C.OUTPUT_DIR / "report_data.json"
    if not data_path.exists():
        raise SystemExit("report_data.json not found — run `python -m solution.run_pipeline` first.")
    data = json.loads(data_path.read_text())
    html = (TEMPLATE
            .replace("__DATA__", json.dumps(data))
            .replace("__GENERATED__", data["generated_at"])
            .replace("__ROWS__", str(data["dataset"]["rows"]))
            .replace("__COLS__", str(data["dataset"]["cols"]))
            .replace("__START__", data["dataset"]["start"][:16])
            .replace("__END__", data["dataset"]["end"][:16]))
    out = C.OUTPUT_DIR / "report.html"
    out.write_text(html)
    print(f"Wrote {out}")

    # Auto-open in the default browser when running on a host (not in Docker,
    # not in CI). Pass --no-open to suppress. Inside a container there is no
    # browser to open, so we print instructions instead.
    in_container = os.path.exists("/.dockerenv")
    suppressed = "--no-open" in sys.argv or os.environ.get("NO_OPEN")
    if not in_container and not suppressed:
        opened = webbrowser.open(out.as_uri())
        print("Opened in your browser." if opened
              else f"Could not auto-open; run:  open {out}")
    else:
        banner = "=" * 64
        print(f"\n{banner}")
        print("  Report built. A container cannot open your browser, so on your")
        print("  HOST machine (from the repo root) run ONE of:")
        print("      open solution/outputs/report.html        # macOS")
        print("      xdg-open solution/outputs/report.html     # Linux")
        print("  Tip: ./solution/run.sh runs everything AND opens it for you.")
        print(f"{banner}\n")


if __name__ == "__main__":
    main()
