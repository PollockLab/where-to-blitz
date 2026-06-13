"""Builds index.html — an interactive trip planner for the Blitz the Gap
"where should I go to record biodiversity?" map. Leaflet basemap (OpenStreetMap +
style switcher); a weight slider per goal blended into a live "impact" score; a
start point + flexible time budget (minutes / hours / days); real driving routes
(OSRM) with drive time, field time, and travel CO2; and a low-carbon ranking
option. Answers "from here, with this much time, where do I maximise my impact?"."""
import json

DATA = json.load(open("cluster_results/webapp_data.json"))
# rows: [lat, lon, discover, conservation, env, staleness, urgency, travel_min, n_train]
OBJ = [
    {"key": "discover",     "name": "Discover the most species", "q": "go where few people have looked"},
    {"key": "conservation", "name": "Find rare species",          "q": "go where range-restricted species already are"},
    {"key": "env",          "name": "Cover every habitat",        "q": "go where the climate is under-sampled"},
    {"key": "staleness",    "name": "Freshest gaps",              "q": "go where no one has been lately"},
    {"key": "urgency",      "name": "Sample before it's lost",    "q": "go where forest cover was recently lost (logging, fire, dieback)"},
]
# order matches OBJ: [discover, conservation, env, staleness, urgency]
# Named Blitz the Gap challenges (real, verified iNaturalist sub-projects), each mapped
# onto our 5 goal axes [discover, conservation, env, staleness, urgency].
PRESETS = [
    {"name": "Biodiversity impact", "w": [0.4, 1.0, 0.6, 0.3, 0.7], "proj": "blitz-the-gap-2026-general",         "blurb": "Balanced — rare & at-risk species, habitat coverage and urgency."},
    {"name": "The Other 99%",       "w": [1, 0, 0.3, 0.2, 0],       "proj": "blitz-the-gap-the-other-99",          "blurb": "Skip the busy 1% — record in Canada's under-sampled 99%."},
    {"name": "Most Wanted",         "w": [0.1, 1, 0, 0, 0.3],       "proj": "blitz-the-gap-canada-s-most-wanted",  "blurb": "Areas rich in range-restricted, at-risk species."},
    {"name": "Too Hot to Handle",   "w": [0.2, 0.4, 0.2, 0, 1],     "proj": "blitz-the-gap-too-hot-to-handle",     "blurb": "Climate-exposed species in the fastest-warming areas."},
    {"name": "Climate Gap",         "w": [0.2, 0, 1, 0.2, 0],       "proj": "blitz-the-gap-closing-the-climate-gap","blurb": "Visit under-sampled climate & habitat types."},
    {"name": "Revisit the Past",    "w": [0.3, 0.2, 0, 1, 0],       "proj": "blitz-the-gap-revisiting-the-past",   "blurb": "Re-find species not recorded in a cell for years."},
]
DEFAULT = PRESETS[0]["w"]

HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Where to Blitz the Gap</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="anonymous"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
  integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin="anonymous"></script>
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"/>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script src="https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.0.22/leaflet-maplibre-gl.js"></script>
<style>
:root{--bg:#0f1620;--panel:#172230;--ink:#e8eef5;--mut:#9fb2c6;--acc:#11a3ff;--gd:#22c55e;--gold:#f0a000}
*{box-sizing:border-box}
html,body{margin:0;height:100%;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--ink)}
#app{display:flex;height:100%}
#panel{width:360px;min-width:360px;height:100%;overflow-y:auto;background:var(--panel);padding:16px 16px 28px;border-right:1px solid #0a1119}
#map{flex:1;height:100%}
h1{font-size:20.5px;margin:0 0 2px}
.sub{color:var(--mut);font-size:14.5px;line-height:1.45;margin:0 0 8px}
.sec{font-size:12.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);margin:18px 0 8px;font-weight:700}
select{padding:7px 8px;background:#0e1722;color:var(--ink);border:1px solid #2a3a4d;border-radius:7px;font-size:16px}
select.full{width:100%}
.obj{margin:10px 0 12px}
.obj .top{display:flex;justify-content:space-between;align-items:baseline}
.obj .nm{font-weight:650;font-size:16px}
.obj .q{color:var(--mut);font-size:13px;margin:1px 0 5px}
.obj .v{color:var(--acc);font-weight:700;font-size:15px;font-variant-numeric:tabular-nums}
input[type=range]{width:100%;accent-color:var(--acc);margin:0}
.presets{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 2px}
.presets button{flex:1 1 auto;background:#10203044;color:var(--ink);border:1px solid #2a3a4d;border-radius:14px;padding:5px 10px;font-size:14px;cursor:pointer}
.presets button:hover,.presets button.on{border-color:var(--acc);color:var(--acc)}
.startrow{display:flex;gap:6px;margin:4px 0}
.startrow button{flex:1;background:#10203044;color:var(--ink);border:1px solid #2a3a4d;border-radius:7px;padding:7px;font-size:14.5px;cursor:pointer}
.startrow button:hover{border-color:var(--gd)}
.startrow button.on{border-color:var(--gd);color:var(--gd);font-weight:700}
.toggle{display:flex;align-items:center;gap:8px;margin:9px 0 2px;font-size:15.5px;cursor:pointer}
.toggle input{width:16px;height:16px;accent-color:var(--gd)}
#plan{width:100%;margin-top:10px;background:var(--gd);color:#04220f;border:0;border-radius:8px;padding:11px;font-size:17px;font-weight:700;cursor:pointer}
#plan:hover{filter:brightness(1.08)}
#trips{margin-top:10px;font-size:15px}
#trips .hd{color:var(--mut);font-size:13px;margin:6px 0 4px}
#trips .row{padding:6px 9px;border-radius:7px;background:#0e1722;margin:5px 0;cursor:pointer;border:1px solid #20303f}
#trips .row:hover,#trips .row.sel{border-color:var(--gold)}
#trips .row .t1{display:flex;justify-content:space-between;font-weight:650}
#trips .row .imp{color:var(--gold)}
#trips .row .t2{color:var(--mut);font-size:14px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#prospects{margin-top:8px}
#prospects .hd{color:var(--mut);font-size:13px;margin:6px 0 5px}
.prospects{display:flex;gap:7px;overflow-x:auto;padding-bottom:4px}
.prospects .sp{flex:0 0 86px;text-decoration:none;color:var(--ink)}
.prospects .sp img{width:86px;height:86px;object-fit:cover;border-radius:8px;display:block;border:1px solid #2a3a4d;background:#0e1722}
.prospects .sp .nm{font-size:12.5px;line-height:1.2;margin-top:3px}
.prospects .sp .ct{font-size:11.5px;color:var(--mut)}
.prospects .rare{background:var(--gold);color:#3a2a00;font-size:10.5px;font-weight:700;padding:0 4px;border-radius:6px;white-space:nowrap}
.prospects .unc{background:#33465a;color:#cfe0ee;font-size:10.5px;font-weight:700;padding:0 4px;border-radius:6px;white-space:nowrap}
.prospects .first{background:var(--gd);color:#04220f;font-size:10.5px;font-weight:700;padding:0 4px;border-radius:6px;white-space:nowrap}
#searchResults{position:absolute;left:0;right:0;top:100%;z-index:1000;background:#0e1722;border:1px solid #2a3a4d;border-radius:7px;margin-top:2px;overflow:hidden;display:none;box-shadow:0 4px 14px rgba(0,0,0,.4)}
#searchResults.open{display:block}
#searchResults .res{padding:8px 10px;cursor:pointer;font-size:13px;line-height:1.3;border-bottom:1px solid #1a2735}
#searchResults .res:last-child{border-bottom:0}
#searchResults .res:hover,#searchResults .res.on{background:#16263a}
#searchResults .res .sub{color:var(--mut);font-size:11px}
.legend{display:flex;align-items:center;gap:8px;margin-top:8px;font-size:13px;color:var(--mut)}
.bar{height:11px;flex:1;border-radius:6px;background:linear-gradient(90deg,#f7fcf5,#e5f5e0,#c7e9c0,#a1d99b,#74c476,#41ab5d,#238b45,#006d2c,#00441b)}
.foot{color:var(--mut);font-size:12.5px;line-height:1.5;margin-top:8px}
#celltable{max-height:300px;overflow:auto;margin-top:6px}
#celltable table{width:100%;border-collapse:collapse;font-size:12px}
#celltable caption{text-align:left;color:var(--mut);font-size:11px;margin-bottom:5px;caption-side:top}
#celltable th,#celltable td{text-align:left;padding:3px 6px;border-bottom:1px solid #243446}
#celltable th{color:var(--mut);font-weight:700}
#celltable tbody tr{cursor:pointer}
#celltable tbody tr:hover,#celltable tbody tr:focus-visible{background:#16263a;outline:2px solid var(--acc);outline-offset:-2px}
details.adv{border-top:1px solid #243446;margin-top:8px}
details.adv>summary{cursor:pointer;list-style:none;padding:10px 0 5px;font-size:12.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);font-weight:700;display:flex;align-items:center;gap:7px}
details.adv>summary::-webkit-details-marker{display:none}
details.adv>summary::before{content:"▸";font-size:11.5px;display:inline-block;transition:transform .15s}
details.adv[open]>summary::before{transform:rotate(90deg)}
details.adv>summary:hover{color:var(--ink)}
.leaflet-popup-content{font-size:15px}.leaflet-popup-content b{color:#0a2a44}
#viewtoggle{position:fixed;top:10px;left:calc(360px + 50%/2 + 90px);transform:translateX(-50%);z-index:1200;display:flex;background:#fff;border-radius:9px;box-shadow:0 2px 9px rgba(0,0,0,.28);overflow:hidden}
#viewtoggle button{border:0;background:#fff;color:#1b2a3a;padding:8px 15px;font-size:15px;font-weight:650;cursor:pointer}
#viewtoggle button.on{background:var(--acc);color:#fff}
#insights{position:fixed;left:360px;top:0;right:0;bottom:0;z-index:1100;background:var(--bg);color:var(--ink);overflow-y:auto;padding:56px 22px 28px;display:none}
#insights .ihd{font-size:16px;line-height:1.55;max-width:980px;margin:0 auto 18px}
#insights .ihd b{color:var(--ink)}
#insights .ctrls{max-width:1200px;margin:0 auto 16px;display:flex;flex-wrap:wrap;gap:20px;font-size:14.5px}
#insights .ctrls .grp{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
#insights .ctrls .lbl{color:var(--mut);font-weight:700;text-transform:uppercase;font-size:11.5px;letter-spacing:.05em;margin-right:3px}
#insights .chip{background:#10203044;border:1px solid #2a3a4d;border-radius:13px;padding:4px 11px;cursor:pointer;user-select:none}
#insights .chip.on{background:var(--acc);border-color:var(--acc);color:#fff}
#insights .chip:focus-visible,.infobtn:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
#insights .matrix{display:grid;gap:10px;max-width:1200px;margin:0 auto;align-items:start}
#insights .gh{font-weight:700;font-size:14.5px;text-align:center;align-self:end;padding-bottom:3px}
#insights .gh .gq{color:var(--mut);font-size:11.5px;font-weight:400;line-height:1.2;display:block;margin-top:1px}
#insights .rl{font-weight:700;font-size:15px;display:flex;align-items:center}
#insights .cell{background:var(--panel);border:1px solid #243446;border-radius:9px;padding:6px;cursor:pointer;transition:border-color .12s}
#insights .cell:hover{border-color:var(--acc)}
#insights .cell canvas{width:100%;height:auto;background:#fbfbf6;border-radius:5px;display:block}
#insights .idis{max-width:980px;margin:18px auto 0;color:var(--mut);font-size:14.5px;line-height:1.6;border-top:1px solid #243446;padding-top:12px}
#insights .idis b{color:var(--ink)}
.sechd{display:flex;align-items:center;gap:6px;margin:18px 0 8px}
.sechd .sec{margin:0}
.infobtn{cursor:pointer;width:16px;height:16px;border-radius:50%;border:1px solid var(--mut);color:var(--mut);font:italic 700 11px/14px Georgia,serif;text-align:center;flex:0 0 auto;user-select:none}
.infobtn:hover{border-color:var(--acc);color:var(--acc)}
.infobox{display:none;background:#0e1722;border:1px solid #2a3a4d;border-radius:8px;padding:9px 11px;margin:0 0 8px;font-size:12px;line-height:1.5;color:var(--mut)}
.infobox.open{display:block}
.infobox b{color:var(--ink)}
.infobox ul{margin:5px 0 0;padding-left:15px}
.infobox li{margin:3px 0}
</style></head>
<body><div id="app">
<div id="panel">
  <h1>Where to <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--gd);text-decoration:underline">Blitz the Gap</a></h1>
  <p class="sub">Choose what counts as <b>impact</b>, then <b>explore</b> the priority map — or <b>plan a trip</b> to the best spot you can reach and get back from.</p>

  <div class="sechd"><span class="sec">Life group & goal</span><span class="infobtn" title="Where do these scores come from?" role="button" tabindex="0" aria-label="About the data" aria-expanded="false" onclick="const b=document.getElementById('taxinfo').classList.toggle('open');this.setAttribute('aria-expanded',b)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.click();}">i</span></div>
  <div class="infobox" id="taxinfo">
    <b>Where the scores come from.</b> Real <a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a> observations in B.C., binned to ~25&nbsp;km cells.
    <ul>
      <li><b>“All biodiversity”</b> averages 7 life groups (amphibians, birds, fungi, insects, mammals, plants, reptiles).</li>
      <li>Each group is a <b>recent sample</b> of iNat records, not the full archive — plants & fungi are capped (~10k each), so their maps are coarser.</li>
      <li>Priority is <b>backtested</b>: holding back later-2025 sightings, higher-priority cells tended to turn up more new species.</li>
      <li>At-risk &amp; location-obscured species are <b>left out of suggestions</b> — we don't point people at them.</li>
    </ul>
    A planning aid, not ground truth — please obscure sensitive species and respect Indigenous data sovereignty. <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">How Blitz the Gap works →</a>
  </div>
  <select id="taxon" class="full" style="margin-bottom:8px"></select>
  <div class="presets" id="presets"></div>
  <div id="challengeBlurb" style="color:var(--mut);font-size:11.5px;line-height:1.4;margin:5px 0 2px"></div>
  <details class="adv"><summary>Fine-tune the five goals</summary><div id="objs"></div></details>

  <div id="tripui">
  <div class="sec">Your trip</div>
  <div style="position:relative">
    <input id="placeSearch" type="text" placeholder="🔍 Search a town or address" autocomplete="off" aria-label="Search for a start place"
      style="width:100%;padding:8px 9px;background:#0e1722;color:var(--ink);border:1px solid #2a3a4d;border-radius:7px;font-size:14px">
    <div id="searchResults"></div>
  </div>
  <div class="startrow" style="margin-top:6px">
    <button id="setMe">📍 My location</button>
    <button id="setVan">Vancouver</button>
  </div>
  <div style="color:var(--mut);font-size:11.5px;margin:4px 0 7px">Start: <b id="startlbl">Vancouver</b> · <b style="color:var(--gd)">tap the map</b> to move it.</div>
  <div class="startrow" id="modes"></div>
  <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
    <span style="font-size:13.5px">Time</span>
    <select id="unit"><option>Minutes</option><option selected>Hours</option><option>Days</option></select>
    <span class="v" id="budv" style="margin-left:auto;color:var(--gold);font-weight:700">5h</span>
  </div>
  <input type="range" id="budget" min="1" max="14" step="0.5" value="5" aria-label="Time budget" style="margin-top:6px">
  <details class="adv"><summary>More options</summary>
    <div style="display:flex;gap:8px;align-items:center;margin:4px 0 8px">
      <span style="font-size:13.5px">Max travel each way</span>
      <select id="maxleg" style="margin-left:auto">
        <option value="0" selected>No limit</option>
        <option value="0.25">15 min</option><option value="0.5">30 min</option>
        <option value="1">1h</option><option value="1.5">1h 30</option>
        <option value="2">2h</option><option value="3">3h</option>
        <option value="4">4h</option><option value="6">6h</option>
        <option value="8">8h</option><option value="12">12h</option>
      </select>
    </div>
    <div style="display:flex;gap:8px;align-items:center;margin:2px 0 6px">
      <span style="font-size:13.5px">Worth the drive</span>
      <select id="minratio" style="margin-left:auto">
        <option value="0">Any trip</option>
        <option value="0.5" selected>Record ≥ ½ the round trip</option>
        <option value="1">Record ≥ the round trip</option>
        <option value="2">Record ≥ 2× the round trip</option>
      </select>
    </div>
    <div style="color:var(--mut);font-size:11px;margin:0 0 8px">Drops mostly-driving trips. <b style="color:var(--ink)">Round trip = there and back</b> (both legs counted). Default: field time ≥ half your round-trip drive; pick "Any" for long hauls.</div>
    <label class="toggle"><input type="checkbox" id="lowc"> ♻︎ Prefer low-carbon trips</label>
    <div style="color:var(--mut);font-size:11px;margin:0 0 5px">Rank by impact per kg of travel CO₂.</div>
    <label class="toggle"><input type="checkbox" id="startProsp"> 🔭 Also show species around my start</label>
  </details>
  <button id="plan">Plan my trip →</button>
  <div id="trips"></div>
  </div>
  <div id="prospects"><div class="hd" style="margin-top:10px">🔭 Tap a cell (or plan a trip) to see what to record there.</div></div>

  <details class="adv"><summary>Map style</summary>
    <select id="basemap" class="full" style="margin-top:4px"></select>
    <div id="layertoggles" style="display:none;margin-top:8px">
      <label class="toggle" style="margin:4px 0"><input type="checkbox" id="tgRoads" checked> Roads</label>
      <label class="toggle" style="margin:4px 0"><input type="checkbox" id="tgLabels" checked> Labels &amp; places</label>
      <div style="color:var(--mut);font-size:11px;line-height:1.4">Vector basemap — toggle layers like Maputnik. Other styles are raster (roads baked in).</div>
    </div>
    <label class="toggle" style="margin:8px 0 2px"><input type="checkbox" id="tgCoverage"> 🛰 iNaturalist coverage</label>
    <div style="color:var(--mut);font-size:11px;line-height:1.4">Where data already is (bright = well-sampled, dark = gaps) — the official "light up the map" layer, for the current group.</div>
    <div style="display:flex;justify-content:space-between;margin:9px 0 0"><span style="font-size:13px">Map brightness</span><span class="v" id="bopv" style="color:var(--acc)">100%</span></div>
    <input type="range" id="baseop" min="0.25" max="1" step="0.05" value="1" aria-label="Map brightness">
  </details>

  <details class="adv"><summary>How impact is scored & data sources</summary>
    <div class="legend" style="margin-top:8px"><span>skip</span><div class="bar"></div><span>go here</span></div>
    <div style="color:var(--mut);font-size:11px;line-height:1.45;margin-top:7px">Each cell's <b style="color:var(--ink)">impact 0–100</b> is your goal mix, relative to the best cell shown. Hover a cell to see which goals drive it.</div>
    <p class="foot"><b style="color:var(--ink)">How it works:</b> <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">Blitz the Gap</a> is a B.C.-wide bioblitz — head to a high-priority spot, record what you see on <a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a>, and your research-grade sightings flow into the <a href="https://www.inaturalist.org/projects/blitz-the-gap-2026-general" target="_blank" rel="noopener" style="color:var(--acc)">2026 project</a>, filling the map's gaps.<br><br>Scores from real iNaturalist observations (BC), tested by holding back later sightings. Drive/cycle/walk routes from OSRM (FOSSGIS); climate from CHELSA; forest loss from Hansen GFC. Driving CO₂ ≈ 0.18 kg/km; cycling/walking zero. A planning aid — obscure sensitive-species locations and respect Indigenous data-sovereignty before any public release.<br><br>This map spans many <b style="color:var(--ink)">Indigenous territories</b> — see whose at <a href="https://native-land.ca" target="_blank" rel="noopener" style="color:var(--acc)">native-land.ca</a>, and seek consent before recording on their lands.</p>
  </details>
  <details class="adv" ontoggle="renderCellTable()"><summary>♿ Top cells (accessible list)</summary><div id="celltable"></div></details>
</div>
<div id="map"></div>
<div id="viewtoggle"><button id="vexplore" class="on">🗺 Explore</button><button id="vplan">🧭 Plan a trip</button><button id="vcompare">📊 Compare goals</button></div>
<div id="insights"></div>
</div>

<script>
const DATA=__DATA__, OBJ=__OBJ__, PRESETS=__PRESETS__, DEFAULT=__DEFAULT__;
const IDX={discover:2,conservation:3,env:4,staleness:5,urgency:6}, TT=7, NTR=8;
const OSRM_BASE="https://routing.openstreetmap.de/";   // FOSSGIS public OSRM (car/bike/foot, CORS-enabled)
const MODES={Walk:{host:'routed-foot',kmh:5,emit:0,icon:'🚶'},Cycle:{host:'routed-bike',kmh:14,emit:0,icon:'🚲'},Drive:{host:'routed-car',kmh:60,emit:0.18,icon:'🚗'}};
const UNITS={Minutes:{toH:1/60,min:15,max:600,step:15,def:120},Hours:{toH:1,min:1,max:14,step:0.5,def:5},Days:{toH:24,min:1,max:21,step:1,def:2}};
const ROAD_FACTOR=1.35, MIN_FIELD_H=0.5, N_CANDIDATES=8;
function co2lbl(kg){return kg<=0?'car-free 🌿':'~'+(kg<10?kg.toFixed(1):Math.round(kg))+' kg CO₂';}
const ICONIC={Amphibia:'Amphibia',Aves:'Aves',Insecta:'Insecta',Mammalia:'Mammalia',Reptilia:'Reptilia',Plantae:'Plantae',Fungi:'Fungi'};
const TAXLBL={Amphibia:'Amphibians',Aves:'Birds',Insecta:'Insects',Mammalia:'Mammals',Reptilia:'Reptiles',Plantae:'Plants',Fungi:'Fungi','All biodiversity':'All biodiversity'};
let prospectSeq=0;
async function fetchProspects(lat,lon,where){
  const myseq=++prospectSeq;
  const pr=document.getElementById('prospects'); pr.innerHTML='<div class="hd">🔭 Looking up what lives here…</div>';
  const ic=ICONIC[state.taxon]||'', HH=0.125;
  const q=(h)=>`https://api.inaturalist.org/v1/observations/species_counts?swlat=${lat-h}&nelat=${lat+h}&swlng=${lon-h}&nelng=${lon+h}&quality_grade=research&taxon_geoprivacy=open&per_page=500&order_by=count`+(ic?`&iconic_taxa=${ic}`:'');
  try{
    const j=await fetch(q(HH)).then(r=>r.json());
    const total=j.total_results||0;
    const cellIds=new Set((j.results||[]).map(r=>r.taxon&&r.taxon.id).filter(Boolean));   // everything already logged in this cell
    let res=(j.results||[]).filter(r=>r.count>=2 && r.taxon && r.taxon.default_photo).map(r=>({count:r.count,taxon:r.taxon,_here:true}));
    let nearby=false;
    if(res.length<4){
      const k=await fetch(q(3*HH)).then(r=>r.json());
      const have=new Set(res.map(r=>r.taxon.id));
      const extra=(k.results||[]).filter(r=>r.taxon&&r.taxon.default_photo&&r.count>=3&&!have.has(r.taxon.id)).map(r=>({count:r.count,taxon:r.taxon,_here:false}));
      if(extra.length){res=res.concat(extra);nearby=true;}
    }
    if(!res.length){pr.innerHTML='<div class="hd">🔭 No research-grade records here yet — you could be the first to document what lives here.</div>';return;}
    res=res.filter(r=>(r.taxon.observations_count||0)>40);                         // drop unverifiable one-offs
    res.sort((a,b)=>(a.taxon.observations_count||1e12)-(b.taxon.observations_count||1e12)); // globally rarest, but present here
    res=res.slice(0,6);
    const ex=`https://www.inaturalist.org/observations?subview=map&swlat=${lat-HH}&nelat=${lat+HH}&swlng=${lon-HH}&nelng=${lon+HH}&quality_grade=research`;
    pr.innerHTML=`<div class="hd">🔭 <b style="color:var(--ink)">${where||'Here'}</b> · ${total.toLocaleString()} species recorded. Keep an eye out for${nearby?' (✦ = nearby)':''}:</div>`+
      '<div class="prospects">'+res.map(r=>{const t=r.taxon,g=t.observations_count||0,rare=g<1500,unc=g<7000;
        return `<a class="sp" href="https://www.inaturalist.org/taxa/${t.id}" target="_blank" rel="noopener" title="${t.name}"><img src="${t.default_photo.square_url}" loading="lazy" alt=""><div class="nm">${r._here?'':'✦ '}${t.preferred_common_name||t.name}${rare?' <span class="rare">rare</span>':(unc?' <span class="unc">uncommon</span>':'')}</div><div class="ct">${r._here?r.count+' here':'nearby'} · ${g.toLocaleString()} worldwide</div></a>`;}).join('')+'</div>'+'<div id="firsts"></div>'+
      `<div style="margin-top:7px;font-size:11.5px"><a href="${ex}" target="_blank" rel="noopener" style="color:var(--acc)">Explore all on iNaturalist →</a> &nbsp;·&nbsp; <a href="https://www.inaturalist.org/observations/new" target="_blank" rel="noopener" style="color:var(--gd)">＋ Log a sighting</a> &nbsp;·&nbsp; <a href="https://www.inaturalist.org/projects/${state.project}" target="_blank" rel="noopener" style="color:var(--mut)">for this challenge</a></div>`;
    // "Fill the gap": species common in the ~50 km neighbourhood but missing from THIS cell's
    // research-grade records (cell query widened to 500 to keep the absence honest). Framed as
    // "missing from this cell", not "nobody has seen it here" -- records-based, not absolute.
    const firsts=await fetchFirsts(lat,lon,ic,cellIds);
    if(myseq!==prospectSeq) return;
    // well-surveyed cell -> a missing common species is a real gap; barely-surveyed cell ->
    // almost everything is "missing", so any record helps (don't overclaim a "gap").
    let nt=0,bd=1e9;for(const m of markers){const dd=Math.abs(m.r[0]-lat)+Math.abs(m.r[1]-lon);if(dd<bd){bd=dd;nt=m.r[8]||0;}}
    const surveyed=nt>=40;
    const fe=document.getElementById('firsts');
    if(fe && firsts.length){
      fe.innerHTML=`<div class="hd" style="margin-top:11px">${surveyed?'🎯 <b style="color:var(--ink)">Fill the gap</b> — common nearby, missing from this well-recorded cell:':'🎯 <b style="color:var(--ink)">Undersampled here</b> — barely recorded; any of these (common nearby) helps:'}</div>`+
        '<div class="prospects">'+firsts.map(r=>{const t=r.taxon,nm=t.preferred_common_name||t.name;return `<a class="sp" href="https://www.inaturalist.org/taxa/${t.id}" target="_blank" rel="noopener" title="${t.name} — common nearby but missing from this cell's records"><img src="${t.default_photo.square_url}" loading="lazy" alt="${nm}"><div class="nm">${nm} <span class="first">gap</span></div><div class="ct">${r.count.toLocaleString()} nearby</div></a>`;}).join('')+'</div>';
    }
    // surface the actionable gap right on the map popup -- no panel scroll needed
    if(where==='Your destination' && destMarker && destMarker.getPopup() && firsts.length){
      const gapThumbs='<div style="font-size:11.5px;color:#1b7837;font-weight:700;margin:9px 0 4px">🎯 Fill the gap — log these here:</div><div style="display:flex;gap:6px">'+firsts.slice(0,4).map(r=>{const t=r.taxon,nm=t.preferred_common_name||t.name;return `<a href="https://www.inaturalist.org/taxa/${t.id}" target="_blank" rel="noopener" style="width:62px;text-decoration:none;color:#0a2a44" title="${t.name}"><img src="${t.default_photo.square_url}" style="width:62px;height:62px;object-fit:cover;border-radius:7px;border:1px solid #cdd;display:block"><div style="font-size:10px;line-height:1.15;margin-top:3px;height:24px;overflow:hidden">${nm}</div></a>`;}).join('')+'</div>';
      destMarker.setPopupContent(destMarker.getPopup().getContent()+gapThumbs); destMarker.openPopup();
    }
  }catch(e){pr.innerHTML='<div class="hd">🔭 Couldn’t load species for this spot.</div>';}
}
async function fetchFirsts(lat,lon,ic,cellIds){
  const R=0.5;   // ~50 km neighbourhood -- same habitat zone, not a different ecoregion
  const u=`https://api.inaturalist.org/v1/observations/species_counts?swlat=${lat-R}&nelat=${lat+R}&swlng=${lon-R}&nelng=${lon+R}&quality_grade=research&taxon_geoprivacy=open&threatened=false&per_page=200&order_by=count`+(ic?`&iconic_taxa=${ic}`:'');
  try{const j=await fetch(u).then(r=>r.json());
    return (j.results||[]).filter(r=>r.taxon&&r.taxon.default_photo&&!cellIds.has(r.taxon.id)&&r.count>=10&&(r.taxon.observations_count||0)>40).slice(0,6);
  }catch(e){return [];}
}
const showProspects=debounce((lat,lon)=>fetchProspects(lat,lon,'Around your start'),500);

const RAMP=[[247,252,245],[229,245,224],[199,233,192],[161,217,155],[116,196,118],[65,171,93],[35,139,69],[0,109,44],[0,68,27]]; // Greens: pale=skip, deep green=GO (traffic-light intuitive, calm)
function colour(t){t=Math.max(0,Math.min(1,t));const x=t*(RAMP.length-1),i=Math.floor(x),f=x-i;const a=RAMP[i],b=RAMP[Math.min(i+1,RAMP.length-1)];return `rgb(${Math.round(a[0]+(b[0]-a[0])*f)},${Math.round(a[1]+(b[1]-a[1])*f)},${Math.round(a[2]+(b[2]-a[2])*f)})`;}
function fmth(h){if(h>=24){const d=Math.floor(h/24),hr=Math.round(h%24);return d+'d'+(hr?(' '+hr+'h'):'');}const m=Math.round(h*60);return m>=60?Math.floor(m/60)+'h'+String(m%60).padStart(2,'0'):m+' min';}
function haversine(a,b,c,d){const R=6371,r=Math.PI/180,x=(c-a)*r,y=(d-b)*r,h=Math.sin(x/2)**2+Math.cos(a*r)*Math.cos(c*r)*Math.sin(y/2)**2;return 2*R*Math.asin(Math.sqrt(h));}

const map=L.map('map',{zoomControl:true}).setView([50.5,-123.5],6);
const ATTR='&copy; OpenStreetMap contributors · routing &copy; OSRM';
const BASEMAPS={
  "OpenStreetMap":{url:'https://tile.openstreetmap.org/{z}/{x}/{y}.png',opt:{maxZoom:19}},
  "Light & muted":{url:'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',opt:{subdomains:'abcd',maxZoom:20}},
  "Dark":{url:'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',opt:{subdomains:'abcd',maxZoom:20}},
  "Roads (Voyager)":{url:'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',opt:{subdomains:'abcd',maxZoom:20}},
  "Satellite":{url:'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',opt:{maxZoom:19}},
  "Terrain":{url:'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',opt:{subdomains:'abc',maxZoom:17}},
  "Vector — toggle layers":{vector:true,style:'https://tiles.openfreemap.org/styles/positron'}
};
let baseLayer=null, baseOpacity=1, glBase=null;
function applyLayerToggles(){
  if(!glBase)return;
  const showRoads=document.getElementById('tgRoads').checked, showLabels=document.getElementById('tgLabels').checked;
  for(const ly of (glBase.getStyle().layers||[])){
    const tag=ly.id+'|'+(ly['source-layer']||'');
    if(ly.type!=='symbol' && /transportation|highway|road|bridge|tunnel|aeroway/i.test(tag))
      glBase.setLayoutProperty(ly.id,'visibility',showRoads?'visible':'none');     // road geometry
    else if(ly.type==='symbol')
      glBase.setLayoutProperty(ly.id,'visibility',showLabels?'visible':'none');     // all text/place/road labels
  }
}
function setBase(name){
  if(baseLayer){map.removeLayer(baseLayer);baseLayer=null;}
  glBase=null;
  const b=BASEMAPS[name], tg=document.getElementById('layertoggles');
  if(b.vector){
    baseLayer=L.maplibreGL({style:b.style,attribution:ATTR}).addTo(map);
    glBase=baseLayer.getMaplibreMap();
    glBase.isStyleLoaded()?applyLayerToggles():glBase.once('load',applyLayerToggles);
    if(glBase.getCanvas())glBase.getCanvas().style.opacity=baseOpacity;
    if(tg)tg.style.display='';
  }else{
    baseLayer=L.tileLayer(b.url,{...b.opt,attribution:ATTR,opacity:baseOpacity}).addTo(map);
    if(baseLayer.bringToBack)baseLayer.bringToBack();
    if(tg)tg.style.display='none';
  }
}
setBase('Light & muted');

let markers=[], routeLine=null, destMarker=null, destCell=null, lastFit=null, lastScored=null, planned=false;
const VAN=[49.28,-123.12];
const w0={}; OBJ.forEach((o,i)=>w0[o.key]=DEFAULT[i]);
const state={taxon:(DATA["All biodiversity"]?"All biodiversity":Object.keys(DATA)[0]), w:w0, start:VAN.slice(), budget:5, maxLeg:0, minRatio:0.5, unit:'Hours', lowc:false, mode:'Drive', startProsp:false, view:'explore', project:'blitz-the-gap-2026-general'};
function debounce(fn,ms){let t;return(...a)=>{clearTimeout(t);t=setTimeout(()=>fn(...a),ms);};}
const replan=debounce(()=>{if(planned)planTrip();},650);

const startIcon=L.divIcon({className:'',iconSize:[20,20],iconAnchor:[10,10],html:'<div style="width:18px;height:18px;border-radius:50%;background:#1f6fe0;border:3px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.45)"></div>'});
const destIcon=L.divIcon({className:'',iconSize:[26,38],iconAnchor:[13,36],html:'<svg width="26" height="38" viewBox="0 0 26 38"><path d="M13 0C6 0 0 6 0 13c0 9 13 25 13 25s13-16 13-25C26 6 20 0 13 0z" fill="#f0a000" stroke="#fff" stroke-width="2"/><circle cx="13" cy="13" r="5" fill="#fff"/></svg>'});
const startMarker=L.marker(state.start,{draggable:true,icon:startIcon,zIndexOffset:1000}).addTo(map).bindTooltip("Start — drag me, or tap the map");
startMarker.on('drag',()=>{const ll=startMarker.getLatLng();state.start=[ll.lat,ll.lng];});
startMarker.on('dragend',()=>{const ll=startMarker.getLatLng();setStart(ll.lat,ll.lng);});
map.on('click',e=>{state.view==='explore'?exploreCell(e.latlng.lat,e.latlng.lng):setStart(e.latlng.lat,e.latlng.lng);});
function setStartLabel(s){document.getElementById('startlbl').textContent=s;}
function setStart(lat,lon,tag){state.start=[lat,lon];startMarker.setLatLng([lat,lon]);
  setStartLabel((tag?tag+' · ':'')+lat.toFixed(3)+', '+lon.toFixed(3));
  geocode(lat,lon,tag); if(state.startProsp) showProspects(lat,lon); replan();}
const geocode=debounce((lat,lon,tag)=>{
  fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=10&lat=${lat}&lon=${lon}`)
    .then(r=>r.json()).then(j=>{const a=j.address||{};const nm=a.city||a.town||a.village||a.hamlet||a.county||a.state||((j.display_name||'').split(',')[0])||'here';
      setStartLabel((tag?tag+' · ':'')+'near '+nm);}).catch(()=>{});},600);

function rows(){return DATA[state.taxon];}
function impact(r){let s=0;for(const o of OBJ)s+=state.w[o.key]*(r[IDX[o.key]]||0);return s;}
function contribs(r){return OBJ.map(o=>({nm:o.name,c:state.w[o.key]*(r[IDX[o.key]]||0),raw:r[IDX[o.key]]||0})).filter(x=>x.c>0).sort((a,b)=>b.c-a.c);}
function contribStr(r){const c=contribs(r).slice(0,3).map(x=>x.nm.toLowerCase()+' '+(x.raw*100|0)); return c.length?c.join(' · '):'—';}

const HALF=0.125;   // half a 0.25-deg cell
function buildMarkers(){
  const rs=rows();
  // The 0.25° grid geometry is identical across taxa, so after the first build a taxon
  // switch only swaps each cell's data row + restyles -- no 3478-rectangle teardown/rebuild.
  if(markers.length===rs.length){markers.forEach((m,i)=>m.r=rs[i]);recolour();return;}
  markers.forEach(m=>map.removeLayer(m.mk));markers=[];
  for(const r of rs){const mk=L.rectangle([[r[0]-HALF,r[1]-HALF],[r[0]+HALF,r[1]+HALF]],{stroke:false,fillOpacity:.5});
    mk.on('click',e=>{state.view==='explore'?exploreCell(r[0],r[1]):setStart(e.latlng.lat,e.latlng.lng);});   // r[0],r[1] are invariant across taxa
    mk.addTo(map);markers.push({mk,r});}
  recolour();
}
function renderCellTable(){
  const el=document.getElementById('celltable');if(!el||!el.closest('details').open)return;
  const top=markers.map(m=>({r:m.r,t:m.t||0})).sort((a,b)=>b.t-a.t).slice(0,40);
  el.innerHTML='<table><caption>Top 40 cells for your goal mix &amp; group, highest first. Tap a row to open it.</caption><thead><tr><th scope="col">#</th><th scope="col">Lat, lon</th><th scope="col">Score</th></tr></thead><tbody>'+
    top.map((o,i)=>`<tr tabindex="0" role="button" data-la="${o.r[0]}" data-lo="${o.r[1]}" aria-label="Rank ${i+1}: latitude ${o.r[0].toFixed(2)}, longitude ${o.r[1].toFixed(2)}, score ${(o.t*100|0)} of 100"><td>${i+1}</td><td>${o.r[0].toFixed(2)}, ${o.r[1].toFixed(2)}</td><td>${(o.t*100|0)}/100</td></tr>`).join('')+'</tbody></table>';
  el.querySelectorAll('tr[data-la]').forEach(tr=>{const go=()=>{const la=+tr.dataset.la,lo=+tr.dataset.lo;map.setView([la,lo],9);state.view==='plan'?setStart(la,lo):exploreCell(la,lo);};tr.onclick=go;tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}};});
}
function recolour(){
  const vals=markers.map(m=>impact(m.r));const lo=Math.min(...vals),hi=Math.max(...vals),rng=(hi-lo)||1;
  markers.forEach((m,i)=>{const t=(vals[i]-lo)/rng;m.t=t;m.mk.setStyle({fillColor:colour(t),fillOpacity:0.1+0.62*t});
    m.mk.bindTooltip(`<b>${(t*100|0)}/100</b> impact for your mix<br><span style="color:#46606f;font-size:11px">driven by: ${contribStr(m.r)}</span><br><span style="color:#8aa;font-size:10px">tap to start a trip here</span>`,{sticky:true,opacity:.97});});
  renderCellTable();
}

// controls
const taxonSel=document.getElementById('taxon');
Object.keys(DATA).forEach(t=>{const o=document.createElement('option');o.value=t;o.textContent=TAXLBL[t]||t;taxonSel.appendChild(o);});
taxonSel.value=state.taxon; taxonSel.onchange=()=>{state.taxon=taxonSel.value;buildMarkers();setCoverage();if(document.getElementById('insights').style.display==='block')renderInsights();};

const objsDiv=document.getElementById('objs');
OBJ.forEach(o=>{const d=document.createElement('div');d.className='obj';
  d.innerHTML=`<div class="top"><span class="nm">${o.name}</span><span class="v" id="v_${o.key}">${state.w[o.key].toFixed(2)}</span></div>
    <div class="q">${o.q}</div><input type="range" id="s_${o.key}" min="0" max="1" step="0.05" value="${state.w[o.key]}" aria-label="${o.name} weight">`;
  objsDiv.appendChild(d);
  d.querySelector('input').addEventListener('input',e=>{state.w[o.key]=parseFloat(e.target.value);
    document.getElementById('v_'+o.key).textContent=state.w[o.key].toFixed(2);markPreset(null);recolour();replan();});});
function applyWeights(arr,name){OBJ.forEach((o,i)=>{state.w[o.key]=arr[i];document.getElementById('s_'+o.key).value=arr[i];document.getElementById('v_'+o.key).textContent=arr[i].toFixed(2);});markPreset(name);recolour();replan();}
const presetsDiv=document.getElementById('presets');
function markPreset(name){[...presetsDiv.children].forEach(b=>b.classList.toggle('on',b.textContent===name));}
function applyChallenge(p){applyWeights(p.w,p.name);state.project=p.proj;
  document.getElementById('challengeBlurb').innerHTML=p.blurb+' <a href="https://www.inaturalist.org/projects/'+p.proj+'" target="_blank" rel="noopener" style="color:var(--acc);white-space:nowrap">join \u2192</a>';}
PRESETS.forEach(p=>{const b=document.createElement('button');b.textContent=p.name;b.title=p.blurb;b.onclick=()=>applyChallenge(p);presetsDiv.appendChild(b);});
applyChallenge(PRESETS[0]);

const bmSel=document.getElementById('basemap');
Object.keys(BASEMAPS).forEach(n=>{const o=document.createElement('option');o.value=o.textContent=n;bmSel.appendChild(o);});
bmSel.value='Light & muted'; bmSel.onchange=()=>setBase(bmSel.value);
let covLayer=null;
const COVTAXA={Plantae:'Plantae',Aves:'Aves',Mammalia:'Mammalia',Insecta:'Insecta',Amphibia:'Amphibia'};   // COGs available per taxon (others -> All)
function setCoverage(){
  if(covLayer){map.removeLayer(covLayer);covLayer=null;}
  if(!document.getElementById('tgCoverage').checked)return;
  const ct=COVTAXA[state.taxon]||'All';
  const cog='https://object-arbutus.cloud.computecanada.ca/bq-io/io/inat_canada_heatmaps/'+ct+'_density_inat_1km.tif';
  covLayer=L.tileLayer('https://tiler.biodiversite-quebec.ca/cog/tiles/{z}/{x}/{y}?url='+encodeURIComponent(cog)+'&rescale=0,10&colormap_name=magma&resampling=cubic',
    {opacity:0.75,maxZoom:14,zIndex:250,attribution:'iNaturalist density &copy; Biodiversit\u00e9 Qu\u00e9bec'}).addTo(map);
}
document.getElementById('tgCoverage').addEventListener('change',setCoverage);
document.getElementById('baseop').addEventListener('input',e=>{baseOpacity=parseFloat(e.target.value);if(baseLayer&&baseLayer.setOpacity)baseLayer.setOpacity(baseOpacity);else if(glBase&&glBase.getCanvas())glBase.getCanvas().style.opacity=baseOpacity;document.getElementById('bopv').textContent=Math.round(baseOpacity*100)+'%';});
['tgRoads','tgLabels'].forEach(id=>document.getElementById(id).addEventListener('change',applyLayerToggles));
document.getElementById('maxleg').addEventListener('change',e=>{state.maxLeg=parseFloat(e.target.value);replan();});
document.getElementById('minratio').addEventListener('change',e=>{state.minRatio=parseFloat(e.target.value);replan();});

const budgetEl=document.getElementById('budget'), budvEl=document.getElementById('budv');
function unitLbl(u,v){return v+(u==='Minutes'?' min':u==='Days'?' d':'h');}
function refreshBudget(){const c=UNITS[state.unit],v=parseFloat(budgetEl.value);state.budget=v*c.toH;budvEl.textContent=unitLbl(state.unit,v);replan();}
document.getElementById('unit').addEventListener('change',e=>{state.unit=e.target.value;const c=UNITS[state.unit];budgetEl.min=c.min;budgetEl.max=c.max;budgetEl.step=c.step;budgetEl.value=c.def;refreshBudget();});
budgetEl.addEventListener('input',refreshBudget);
document.getElementById('lowc').addEventListener('change',e=>{state.lowc=e.target.checked;if(lastFit)rankAndRender();});
document.getElementById('startProsp').addEventListener('change',e=>{state.startProsp=e.target.checked; if(state.startProsp) fetchProspects(state.start[0],state.start[1],'Around your start');});

const modesDiv=document.getElementById('modes');
Object.keys(MODES).forEach(mn=>{const b=document.createElement('button');b.textContent=MODES[mn].icon+' '+mn;b.dataset.m=mn;
  b.onclick=()=>{state.mode=mn;[...modesDiv.children].forEach(x=>x.classList.toggle('on',x.dataset.m===mn));replan();};
  modesDiv.appendChild(b);});
[...modesDiv.children].forEach(x=>x.classList.toggle('on',x.dataset.m===state.mode));

document.getElementById('setVan').onclick=()=>{map.panTo(VAN);setStart(VAN[0],VAN[1],'Vancouver');};
// type a town/address -> Nominatim forward geocoding (biased to BC/Canada), pick from a dropdown
const psEl=document.getElementById('placeSearch'), srEl=document.getElementById('searchResults');
function closeResults(){srEl.classList.remove('open');srEl.innerHTML='';}
const doSearch=debounce(async q=>{
  if(q.trim().length<3){closeResults();return;}
  try{
    const u=`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&countrycodes=ca&viewbox=-139,60,-114,48&q=${encodeURIComponent(q)}`;
    const r=await fetch(u,{headers:{'Accept-Language':'en'}}).then(r=>r.json());
    if(psEl.value.trim()!==q.trim())return;                       // stale
    if(!r.length){const d=document.createElement('div');d.className='res';d.style.cssText='color:var(--mut);cursor:default';d.textContent='No places found';srEl.replaceChildren(d);srEl.classList.add('open');return;}
    srEl.replaceChildren(...r.map(p=>{const parts=p.display_name.split(', '),head=parts[0],sub=parts.slice(1,4).join(', ');
      const d=document.createElement('div');d.className='res';d.dataset.lat=p.lat;d.dataset.lon=p.lon;
      const b=document.createElement('b');b.textContent=head;const sb=document.createElement('div');sb.className='sub';sb.textContent=sub;d.append(b,sb);
      d.onclick=()=>{map.setView([+p.lat,+p.lon],10);setStart(+p.lat,+p.lon,head);psEl.value=head;closeResults();};
      return d;}));
    srEl.classList.add('open');
  }catch(e){closeResults();}
},450);
psEl.addEventListener('input',e=>doSearch(e.target.value));
psEl.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();const f=srEl.querySelector('.res[data-lat]');if(f)f.click();}else if(e.key==='Escape')closeResults();});
document.addEventListener('click',e=>{if(!srEl.contains(e.target)&&e.target!==psEl)closeResults();});
document.getElementById('setMe').onclick=()=>{setStartLabel('locating…');
  const ok=(lat,lon,how)=>{map.panTo([lat,lon]);setStart(lat,lon,how);};
  let done=false;
  if(navigator.geolocation){navigator.geolocation.getCurrentPosition(p=>{done=true;ok(p.coords.latitude,p.coords.longitude,'my location');},()=>{if(!done)ipLoc(ok);},{timeout:6000});setTimeout(()=>{if(!done)ipLoc(ok);},6500);}else ipLoc(ok);};
function ipLoc(ok){fetch('https://ipapi.co/json/').then(r=>r.json()).then(j=>{if(j&&j.latitude)ok(j.latitude,j.longitude,'my area (from IP)');else setStartLabel('location unavailable');}).catch(()=>setStartLabel('location unavailable'));}

// the trip planner
document.getElementById('plan').onclick=async()=>{await planTrip();const t=document.getElementById('trips');if(t)t.scrollIntoView({behavior:'smooth',block:'start'});};
async function planTrip(){
  planned=true;
  const trips=document.getElementById('trips');const [slat,slon]=state.start,budget=state.budget,M=MODES[state.mode];
  const all=markers.map(m=>{const km=haversine(slat,slon,m.r[0],m.r[1]);return {m,km,estOne:(km*ROAD_FACTOR)/M.kmh,imp:impact(m.r)};});
  // Pick candidates BEFORE we know routability, so blend two signals: the highest-impact
  // cells AND the nearest cells. Picking by impact alone can hand back 8 cells that all
  // sit across water/mountains (OSRM can't reach them) and leave nothing to show.
  const cap=state.maxLeg>0?state.maxLeg:Infinity;   // optional "max each way" preference
  const within=all.filter(c=>2*c.estOne<=budget-MIN_FIELD_H && c.estOne<=cap*1.5);
  const pool=within.length?within:all.filter(c=>c.estOne<=cap*1.5);   // never dead-end: nearest overall, still within the cap
  const byImp=pool.slice().sort((a,b)=>b.imp-a.imp).slice(0,N_CANDIDATES);
  const byNear=pool.slice().sort((a,b)=>a.km-b.km).slice(0,Math.ceil(N_CANDIDATES/2));
  const cand=[...new Set([...byImp,...byNear])].slice(0,N_CANDIDATES+4);
  trips.innerHTML='<div class="hd">Finding real '+state.mode.toLowerCase()+' routes…</div>';
  const out=await Promise.all(cand.map(async c=>{
    try{const u=`${OSRM_BASE}${M.host}/route/v1/driving/${slon},${slat};${c.m.r[1]},${c.m.r[0]}?overview=full&geometries=geojson`;const j=await fetch(u).then(r=>r.json());
      if(j.code==='Ok'&&j.routes&&j.routes[0]){const rt=j.routes[0];return {...c,oneH:rt.duration/3600,oneKm:rt.distance/1000,geo:rt.geometry,real:true};}}catch(e){}
    return {...c,oneH:c.estOne,oneKm:c.km*ROAD_FACTOR,geo:null,real:false};}));
  const scored=out.map(o=>({...o,fieldH:budget-2*o.oneH,co2:2*o.oneKm*M.emit}));
  // keep trips that fit the budget, honour the "max each way" cap, and the "worth the
  // drive" ratio (record at least state.minRatio x the round-trip drive time).
  const ratio=state.minRatio||0;
  const fit=scored.filter(o=>o.oneH<=cap && o.fieldH>=MIN_FIELD_H && o.fieldH>=ratio*2*o.oneH);
  // "Right where you are" — the nearest cell, zero travel. Always an option: if you
  // got somewhere remote with no road out within budget, the obvious move is to record
  // the gap you're standing in. It ranks high in a remote gap, low in a sampled city.
  const here0=all.reduce((a,b)=>a.km<b.km?a:b);
  const here={...here0,oneH:0,oneKm:0,geo:null,real:true,fieldH:budget,co2:0,here:true};
  lastFit=[here,...fit];lastScored=scored.filter(o=>o.oneH<=cap);
  rankAndRender();
}
function tripRow(trips,o,id,num){
  o.id='t'+id;const div=document.createElement('div');div.className='row';div.dataset.id=o.id;
  const np=num?num+'. ':'';
  const title=o.here?np+'Right where you are':np+o.m.r[0].toFixed(2)+', '+o.m.r[1].toFixed(2);
  const line2=o.here
    ?`📍 no extra travel — record the cell you're already in`
    :`${MODES[state.mode].icon} ${fmth(o.oneH)} each way · ${o.fieldH>=MIN_FIELD_H?fmth(o.fieldH)+' field':fmth(2*o.oneH)+' round · over '+budvEl.textContent} · ${co2lbl(o.co2)}`;
  div.innerHTML=`<div class="t1"><span>${title}</span><span class="imp">${(o.m.t*100|0)}/100</span></div><div class="t2">${line2}</div>`;
  div.onclick=()=>selectTrip(o);trips.appendChild(div);
}
function subhd(trips,html){const hd=document.createElement('div');hd.className='hd';hd.style.marginTop='12px';hd.innerHTML=html;trips.appendChild(hd);}
function rankAndRender(){
  // Travel trips ranked by impact x field-time (rewards nearby: more field time, less
  // driving), /CO2 when low-carbon is on. "Right where you are" is kept OUT of this
  // ranking -- zero travel + full field time would always win and defeat the point of
  // a gap-finder -- and offered as a distinct option instead.
  const rv=o=>{const base=(o.m.t||0)*Math.max(o.fieldH,0); return state.lowc?base/(1+o.co2):base;};
  const here=lastFit.find(o=>o.here);
  const trips=lastFit.filter(o=>!o.here).sort((a,b)=>rv(b)-rv(a));
  const el=document.getElementById('trips');
  if(trips.length){
    const est=trips.some(o=>!o.real);
    el.innerHTML='<div class="hd">Best trips within '+budvEl.textContent+' — most <b style="color:var(--ink)">impact × time you\'d get to record</b>'+(state.lowc?', per kg CO₂':'')+(est?' (some times estimated)':'')+':</div>';
    trips.slice(0,5).forEach((o,i)=>tripRow(el,o,i,i+1));
    subhd(el,'Or skip the drive:');tripRow(el,here,'here',0);
    selectTrip(trips[0]);
  } else {
    el.innerHTML='<div class="hd">No round trip fits '+budvEl.textContent+' by '+state.mode.toLowerCase()+' from here. The best move is to record where you are — or go farther with more time:</div>';
    tripRow(el,here,'here',1);
    const far=lastScored.slice().sort((a,b)=>a.oneH-b.oneH).slice(0,3);
    if(far.length){subhd(el,'Farther afield — over '+budvEl.textContent+' round trip, but yours with more time'+(state.mode!=='Drive'?' or by 🚗':'')+':');far.forEach((o,i)=>tripRow(el,o,'f'+i,0));}
    selectTrip(here);
  }
}
function clearRoute(){[routeLine,destMarker,destCell].forEach(l=>{if(l)map.removeLayer(l);});routeLine=destMarker=destCell=null;}
function selectTrip(o){clearRoute();const dest=[o.m.r[0],o.m.r[1]];
  destCell=L.rectangle([[dest[0]-0.125,dest[1]-0.125],[dest[0]+0.125,dest[1]+0.125]],{color:'#1b7837',weight:2,dashArray:'5 5',fillColor:'#74c476',fillOpacity:0.16,interactive:false}).addTo(map);
  if(o.here){
    map.fitBounds(destCell.getBounds(),{padding:[90,90],maxZoom:11});
    destMarker=L.marker(dest,{icon:destIcon,zIndexOffset:900}).addTo(map)
      .bindPopup(`<b>Record right where you are</b><br><span style="color:#667">you're in this ~25 km cell — no extra travel needed</span><br>impact <b>${(o.m.t*100|0)}/100</b> · ${contribStr(o.m.r)}<br>📍 spend your time recording, not driving here`).openPopup();
    document.querySelectorAll('#trips .row').forEach(el=>el.classList.toggle('sel',el.dataset.id===o.id));
    fetchProspects(dest[0],dest[1],'Right where you are');return;
  }
  const layers=[];
  if(o.geo){
    // OSRM snaps the route ends to the nearest road, so its geometry starts/ends
    // a little off the actual start pin and cell centre. Draw the routed road solid,
    // and bridge the off-road hops (pin->road, road->cell) with a faint dashed link
    // so the line always visibly connects to where you are and where you're going.
    const cs=o.geo.coordinates.map(c=>[c[1],c[0]]);
    layers.push(L.polyline(cs,{color:'#ffffff',weight:9,opacity:.95}),
                L.polyline(cs,{color:'#1f6fe0',weight:4.5,opacity:1}),
                L.polyline([state.start,cs[0]],{color:'#1f6fe0',weight:2.5,dashArray:'2 6',opacity:.8}),
                L.polyline([cs[cs.length-1],dest],{color:'#1f6fe0',weight:2.5,dashArray:'2 6',opacity:.8}));
  } else {
    layers.push(L.polyline([state.start,dest],{color:'#ffffff',weight:8,opacity:.95}),
                L.polyline([state.start,dest],{color:'#1f6fe0',weight:4,dashArray:'7 7',opacity:1}));
  }
  routeLine=L.featureGroup(layers).addTo(map);map.fitBounds(routeLine.getBounds(),{padding:[60,60],maxZoom:10});
  destMarker=L.marker(dest,{icon:destIcon,zIndexOffset:900}).addTo(map)
    .bindPopup(`<b>Go to this area</b> <span style="color:#667">— anywhere in the highlighted ~25 km cell</span><br><span style="color:#667">centre ${dest[0].toFixed(2)}, ${dest[1].toFixed(2)}</span><br>impact <b>${(o.m.t*100|0)}/100</b> · ${contribStr(o.m.r)}<br>${o.fieldH>=MIN_FIELD_H?`${MODES[state.mode].icon} ${fmth(o.oneH)} each way · ${fmth(o.fieldH)} in the field`:`${MODES[state.mode].icon} ${fmth(o.oneH)} each way · ${fmth(2*o.oneH)} round trip — over your ${budvEl.textContent}`}<br>${co2lbl(o.co2)} round trip${o.real?'':' (estimated)'}`).openPopup();
  document.querySelectorAll('#trips .row').forEach(el=>el.classList.toggle('sel',el.dataset.id===o.id));
  fetchProspects(o.m.r[0],o.m.r[1],'Your destination');
}

// ---- Insights view: the §2 figure, interactive ----
function rankArr(a){const idx=a.map((v,i)=>[v,i]).sort((x,y)=>x[0]-y[0]);const r=new Array(a.length);
  let i=0;while(i<idx.length){let j=i;while(j+1<idx.length&&idx[j+1][0]===idx[i][0])j++;const avg=(i+j)/2;for(let k=i;k<=j;k++)r[idx[k][1]]=avg;i=j+1;}  // average ranks for ties (proper Spearman; thousands of tied zeros otherwise distort it)
  return r;}
function spear(a,b){const ra=rankArr(a),rb=rankArr(b),n=a.length;let ma=(n-1)/2,num=0,da=0,db=0;for(let i=0;i<n;i++){const x=ra[i]-ma,y=rb[i]-ma;num+=x*y;da+=x*x;db+=y*y;}return da&&db?num/Math.sqrt(da*db):0;}
const BB={minlon:-139,maxlon:-114,minlat:48,maxlat:60};
function drawMini(cv,rows,gi){
  const ctx=cv.getContext('2d'),W=cv.width,H=cv.height;ctx.clearRect(0,0,W,H);
  const vals=rows.map(r=>r[2+gi]),lo=Math.min(...vals),hi=Math.max(...vals),rng=(hi-lo)||1;
  const cw=W/((BB.maxlon-BB.minlon)/0.25)+0.6,ch=H/((BB.maxlat-BB.minlat)/0.25)+0.6;
  for(const r of rows){const t=(r[2+gi]-lo)/rng;
    const x=(r[1]-BB.minlon)/(BB.maxlon-BB.minlon)*W,y=(1-(r[0]-BB.minlat)/(BB.maxlat-BB.minlat))*H;
    ctx.fillStyle=colour(t);ctx.globalAlpha=0.14+0.72*t;ctx.fillRect(x-cw/2,y-ch/2,cw,ch);}
  ctx.globalAlpha=1;
}
function renderInsights(){
  const ins=document.getElementById('insights'), taxa=Object.keys(DATA);
  if(!state.insTaxa) state.insTaxa=taxa.includes('All biodiversity')?['All biodiversity']:[taxa[0]];   // comprehensive overview by default; add groups to compare across taxa
  if(!state.insGoals) state.insGoals=[0,1,2,3,4];
  const goalsOn=OBJ.map((o,i)=>i).filter(i=>state.insGoals.includes(i));
  const rowsTaxa=state.insTaxa.length?state.insTaxa:[taxa[0]];
  let html='<div class="ihd"><b>The same place — different goals, different life groups.</b> Each map shades every B.C. cell by one goal (<b>darker = go there</b>). The hot zones shift between goals (a value choice) and between groups (different species fill different gaps). Pick the rows &amp; columns; tap any map to open it in the planner.</div>';
  html+='<div class="ctrls"><div class="grp"><span class="lbl">Groups (rows)</span>'+taxa.map(t=>`<span class="chip ${state.insTaxa.includes(t)?'on':''}" role="button" tabindex="0" aria-pressed="${state.insTaxa.includes(t)}" data-tx="${t}">${TAXLBL[t]||t}</span>`).join('')+'</div>';
  html+='<div class="grp"><span class="lbl">Goals (columns)</span>'+OBJ.map((o,i)=>`<span class="chip ${state.insGoals.includes(i)?'on':''}" role="button" tabindex="0" aria-pressed="${state.insGoals.includes(i)}" data-gl="${i}">${o.name}</span>`).join('')+'</div></div>';
  html+=`<div class="matrix" style="grid-template-columns:100px repeat(${goalsOn.length},1fr)"><div></div>`;
  goalsOn.forEach(gi=>html+=`<div class="gh">${OBJ[gi].name}<span class="gq">${OBJ[gi].q}</span></div>`);
  rowsTaxa.forEach(t=>{html+=`<div class="rl">${TAXLBL[t]||t}</div>`;goalsOn.forEach(gi=>html+=`<div class="cell" data-tx="${t}" data-gl="${gi}"><canvas width="220" height="170"></canvas></div>`);});
  html+='</div><div class="idis" id="idis"></div>';
  ins.innerHTML=html;
  ins.querySelectorAll('.cell').forEach(c=>{drawMini(c.querySelector('canvas'),DATA[c.dataset.tx],+c.dataset.gl);
    c.onclick=()=>{const t=c.dataset.tx,gi=+c.dataset.gl;taxonSel.value=t;state.taxon=t;buildMarkers();applyWeights(OBJ.map((_,i)=>i===gi?1:0),OBJ[gi].name);setView('plan');};});
  ins.querySelectorAll('.chip[data-tx]').forEach(ch=>{ch.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();ch.click();}};ch.onclick=()=>{const t=ch.dataset.tx,i=state.insTaxa.indexOf(t);if(i>=0){if(state.insTaxa.length>1)state.insTaxa.splice(i,1);}else state.insTaxa.push(t);renderInsights();};});
  ins.querySelectorAll('.chip[data-gl]').forEach(ch=>{ch.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();ch.click();}};ch.onclick=()=>{const g=+ch.dataset.gl,i=state.insGoals.indexOf(g);if(i>=0){if(state.insGoals.length>1)state.insGoals.splice(i,1);}else state.insGoals.push(g);state.insGoals.sort((x,y)=>x-y);renderInsights();};});
  const disVals=rowsTaxa.map(t=>{const base=DATA[t].filter(r=>r[8]>0),b=base.length>=20?base:DATA[t];return {t,rho:spear(b.map(r=>r[2]),b.map(r=>r[3]))};});
  const dis=disVals.map(d=>`${TAXLBL[d.t]||d.t} <b>${d.rho.toFixed(2)}</b>`).join(' · ');
  const neg=disVals.filter(d=>d.rho<0).length, n=disVals.length;   // verdict follows the actual signs, not a hardcoded claim
  const verdict = neg===n ? 'The two goals point to <b>different places in every group shown</b> — chasing quantity is not the same as chasing rarity.'
    : neg===0 ? 'For the groups shown, the two goals mostly <b>agree</b> here.'
    : `They point to different places in <b>${neg} of ${n}</b> groups shown — quantity and rarity often diverge, but not always.`;
  document.getElementById('idis').innerHTML=`<b>Discover vs. find-rare-species</b> — Spearman ρ between cell rankings (over cells with records; negative = opposite places): ${dis}. ${verdict}`;
}
// Explore mode: tap a cell to see its score + what to record there (no trip planning).
function exploreCell(lat,lon){
  let best=markers[0],bd=1e9;for(const m of markers){const d=Math.abs(m.r[0]-lat)+Math.abs(m.r[1]-lon);if(d<bd){bd=d;best=m;}}
  const o=best,dest=[o.r[0],o.r[1]];clearRoute();
  destCell=L.rectangle([[dest[0]-0.125,dest[1]-0.125],[dest[0]+0.125,dest[1]+0.125]],{color:'#1b7837',weight:2,dashArray:'5 5',fillColor:'#74c476',fillOpacity:0.16,interactive:false}).addTo(map);
  destMarker=L.marker(dest,{icon:destIcon,zIndexOffset:900}).addTo(map)
    .bindPopup(`<b>This area</b> — anywhere in the highlighted ~25 km cell<br><span style="color:#667">centre ${dest[0].toFixed(2)}, ${dest[1].toFixed(2)}</span><br>impact <b>${(o.t*100|0)}/100</b> · ${contribStr(o.r)}`).openPopup();
  fetchProspects(dest[0],dest[1],'Your destination');
}
function setView(v){
  state.view=v;
  document.getElementById('insights').style.display=v==='compare'?'block':'none';
  document.getElementById('tripui').style.display=v==='plan'?'':'none';
  document.getElementById('vexplore').classList.toggle('on',v==='explore');
  document.getElementById('vplan').classList.toggle('on',v==='plan');
  document.getElementById('vcompare').classList.toggle('on',v==='compare');
  if(v!=='plan')clearRoute();
  if(v==='compare')renderInsights(); else map.invalidateSize();
}
document.getElementById('vexplore').onclick=()=>setView('explore');
document.getElementById('vplan').onclick=()=>setView('plan');
document.getElementById('vcompare').onclick=()=>setView('compare');

buildMarkers();
setView('explore');
</script></body></html>"""

out = (HTML.replace("__DATA__", json.dumps(DATA, separators=(",", ":")))
           .replace("__OBJ__", json.dumps(OBJ))
           .replace("__PRESETS__", json.dumps(PRESETS))
           .replace("__DEFAULT__", json.dumps(DEFAULT)))
open("index.html", "w").write(out)
print("wrote index.html  ({:.0f} KB)".format(len(out) / 1024))
