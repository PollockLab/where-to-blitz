"""Builds index.html — an interactive trip planner for the Blitz the Gap
"where should I go to record biodiversity?" map. Leaflet basemap (OpenStreetMap +
style switcher); a weight slider per goal blended into a live "impact" score; a
start point + flexible time budget (minutes / hours / days); real driving routes
(OSRM) with drive time, field time, and travel CO2; and a low-carbon ranking
option. Answers "from here, with this much time, where do I maximise my impact?"."""
import json

# Canada-wide: fetch per-group at runtime (38k cells x 8 groups is too big to inline).
# Inject only the group->filename map; the browser fetches each group's JSON on demand.
CA_INDEX = json.load(open("cluster_results/ca/index.json"))
FILES = CA_INDEX["files"]
# rows: [lat, lon, discover, conservation, env, staleness, urgency, travel_min, n_train]
OBJ = [
    {"key": "discover",     "name": "Discover the most species", "q": "go where few people have looked"},
    {"key": "conservation", "name": "Find rare species",          "q": "go where range-restricted species are \u2014 national layer in progress"},
    {"key": "env",          "name": "Cover every habitat",        "q": "go where the climate is under-sampled"},
    {"key": "staleness",    "name": "Freshest gaps",              "q": "go where no one has been lately \u2014 approximate nationally"},
    {"key": "urgency",      "name": "Sample before it's lost",    "q": "go where forest cover was recently lost (logging, fire, dieback)"},
]
# order matches OBJ: [discover, conservation, env, staleness, urgency]
# Named Blitz the Gap challenges (real, verified iNaturalist sub-projects), each mapped
# onto our 5 goal axes [discover, conservation, env, staleness, urgency].
PRESETS = [
    {"name": "Biodiversity impact", "w": [0.8, 0.2, 0.8, 0.4, 0.2], "proj": "blitz-the-gap-2026-general",         "blurb": "Under-sampling + climate gaps — the strongest signal nationwide."},
    {"name": "The Other 99%",       "w": [1, 0, 0.3, 0.2, 0],       "proj": "blitz-the-gap-the-other-99",          "blurb": "Skip the busy 1% — record in Canada's under-sampled 99%."},
    {"name": "Most Wanted",         "w": [0.1, 1, 0, 0, 0.3],       "proj": "blitz-the-gap-canada-s-most-wanted",  "blurb": "Range-restricted, at-risk species — national rarity layer still in progress."},
    {"name": "Too Hot to Handle",   "w": [0.2, 0.4, 0.2, 0, 1],     "proj": "blitz-the-gap-too-hot-to-handle",     "blurb": "Climate-exposed species in the fastest-warming areas."},
    {"name": "Climate Gap",         "w": [0.2, 0, 1, 0.2, 0],       "proj": "blitz-the-gap-closing-the-climate-gap","blurb": "Visit under-sampled climate & habitat types."},
    {"name": "Revisit the Past",    "w": [0.3, 0.2, 0, 1, 0],       "proj": "blitz-the-gap-revisiting-the-past",   "blurb": "Cells overlooked lately — staleness is approximate nationally."},
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
.langtoggle{display:flex;gap:4px;float:right;margin:2px 0 0}
.langtoggle button{background:#10203044;color:var(--mut);border:1px solid #2a3a4d;border-radius:6px;padding:3px 9px;font-size:12.5px;font-weight:700;cursor:pointer;letter-spacing:.03em}
.langtoggle button:hover{border-color:var(--acc);color:var(--ink)}
.langtoggle button.on{border-color:var(--acc);background:var(--acc);color:#fff}
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
@media(max-width:640px){
  #app{flex-direction:column}
  #panel{width:100%;min-width:0;height:auto;max-height:50vh;border-right:0;border-bottom:1px solid #0a1119}
  #map{height:50vh;flex:none}
  #viewtoggle{left:50%;top:auto;bottom:12px}
  #insights{left:0;top:0;height:100%;padding:54px 12px 24px}
}
#maplegend{position:fixed;bottom:14px;left:372px;z-index:1050;background:rgba(255,255,255,.93);border-radius:9px;padding:8px 11px;box-shadow:0 2px 10px rgba(0,0,0,.22);font-size:12px;color:#16233a;max-width:212px;line-height:1.3}
#maplegend .lt{font-weight:700;font-size:12.5px}
#maplegend .ramp{height:9px;border-radius:5px;background:linear-gradient(90deg,#f7fcf5,#c7e9c0,#74c476,#238b45,#00441b);margin:4px 0 2px}
#maplegend .lab{display:flex;justify-content:space-between;font-size:10.5px;color:#5a6b7a}
#maplegend .hint{margin-top:4px;color:#5a6b7a;font-size:10.5px}
@media(max-width:640px){#maplegend{left:8px;bottom:auto;top:calc(50vh + 8px);max-width:158px;padding:6px 8px}}
#loading{position:fixed;left:calc(360px + (100% - 360px)/2);top:46%;transform:translate(-50%,-50%);z-index:1200;background:rgba(255,255,255,.96);border-radius:10px;padding:11px 17px;box-shadow:0 3px 14px rgba(0,0,0,.25);font-size:14px;font-weight:600;color:#1b2a3a}
@media(max-width:640px){#loading{left:50%;top:74vh}}
</style></head>
<body><div id="app">
<div id="panel">
  <div class="langtoggle" role="group" aria-label="Language"><button id="lang-en" type="button">EN</button><button id="lang-fr" type="button">FR</button></div>
  <h1 data-i18n-html="title_full">Where to <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--gd);text-decoration:underline">Blitz the Gap</a></h1>
  <p class="sub" data-i18n-html="sub">Choose what counts as <b>impact</b>, then <b>explore</b> the priority map — or <b>plan a trip</b> to the best spot you can reach and get back from.</p>

  <div class="sechd"><span class="sec" data-i18n="sec_group">Life group & goal</span><span class="infobtn" data-i18n-title="info_btn" title="Where do these scores come from?" role="button" tabindex="0" aria-label="About the data" aria-expanded="false" onclick="const b=document.getElementById('taxinfo').classList.toggle('open');this.setAttribute('aria-expanded',b)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.click();}">i</span></div>
  <div class="infobox" id="taxinfo" data-i18n-html="taxinfo">
    <b>Where the scores come from.</b> Canada-wide, on a 0.25° (~25&nbsp;km) grid.
    <ul>
      <li><b>Nationally the real signals are <span style="color:var(--ink)">under-sampling</span> (“Discover the most species”), <span style="color:var(--ink)">climate-coverage</span> (“Cover every habitat”) and <span style="color:var(--ink)">recent forest loss</span> (“Sample before it’s lost”).</b> Computed from real data (<a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a> density + CHELSA climate + Hansen forest loss); travel time is real too.</li>
      <li><b>Find rare species and Freshest gaps are approximate / being improved</b> — there’s no national rarity layer or per-record dates yet, so those are placeholders (conservation ≈ 0; staleness ≈ inverse density).</li>
      <li>The original <b>B.C. pilot had all five axes from real iNat history</b>; the national rollout has four of five real and is filling in the last (rare species).</li>
      <li><b>“All biodiversity”</b> averages 7 life groups (amphibians, birds, fungi, insects, mammals, plants, reptiles).</li>
    </ul>
    A planning aid, not ground truth — please obscure sensitive species and respect Indigenous data sovereignty. <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">How Blitz the Gap works →</a>
  </div>
  <select id="taxon" class="full" style="margin-bottom:8px"></select>
  <div class="presets" id="presets"></div>
  <div id="challengeBlurb" style="color:var(--mut);font-size:11.5px;line-height:1.4;margin:5px 0 2px"></div>
  <details class="adv"><summary data-i18n="finetune">Fine-tune the five goals</summary><div id="objs"></div></details>

  <div id="tripui">
  <div class="sec" data-i18n="your_trip">Your trip</div>
  <div style="position:relative">
    <input id="placeSearch" type="text" placeholder="🔍 Search a town or address" data-i18n-ph="search_ph" autocomplete="off" aria-label="Search for a start place"
      style="width:100%;padding:8px 9px;background:#0e1722;color:var(--ink);border:1px solid #2a3a4d;border-radius:7px;font-size:14px">
    <div id="searchResults"></div>
  </div>
  <div class="startrow" style="margin-top:6px">
    <button id="setMe" data-i18n="my_location">📍 My location</button>
    <button id="setVan" data-i18n="vancouver">Vancouver</button>
  </div>
  <div style="color:var(--mut);font-size:11.5px;margin:4px 0 7px" data-i18n-html="start_hint">Start: <b id="startlbl">Vancouver</b> · <b style="color:var(--gd)">tap the map</b> to move it.</div>
  <div class="startrow" id="modes"></div>
  <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
    <span style="font-size:13.5px" data-i18n="time">Time</span>
    <select id="unit"><option value="Minutes" data-i18n="unit_minutes">Minutes</option><option value="Hours" selected data-i18n="unit_hours">Hours</option><option value="Days" data-i18n="unit_days">Days</option></select>
    <span class="v" id="budv" style="margin-left:auto;color:var(--gold);font-weight:700">5h</span>
  </div>
  <input type="range" id="budget" min="1" max="14" step="0.5" value="5" aria-label="Time budget" style="margin-top:6px">
  <details class="adv"><summary data-i18n="more_options">More options</summary>
    <div style="display:flex;gap:8px;align-items:center;margin:4px 0 8px">
      <span style="font-size:13.5px" data-i18n="max_travel">Max travel each way</span>
      <select id="maxleg" style="margin-left:auto">
        <option value="0" selected data-i18n="no_limit">No limit</option>
        <option value="0.25">15 min</option><option value="0.5">30 min</option>
        <option value="1">1h</option><option value="1.5">1h 30</option>
        <option value="2">2h</option><option value="3">3h</option>
        <option value="4">4h</option><option value="6">6h</option>
        <option value="8">8h</option><option value="12">12h</option>
      </select>
    </div>
    <div style="display:flex;gap:8px;align-items:center;margin:2px 0 6px">
      <span style="font-size:13.5px" data-i18n="worth_drive">Worth the drive</span>
      <select id="minratio" style="margin-left:auto">
        <option value="0" data-i18n="ratio_any">Any trip</option>
        <option value="0.5" selected data-i18n="ratio_half">Record ≥ ½ the round trip</option>
        <option value="1" data-i18n="ratio_one">Record ≥ the round trip</option>
        <option value="2" data-i18n="ratio_two">Record ≥ 2× the round trip</option>
      </select>
    </div>
    <div style="color:var(--mut);font-size:11px;margin:0 0 8px" data-i18n-html="worth_hint">Drops mostly-driving trips. <b style="color:var(--ink)">Round trip = there and back</b> (both legs counted). Default: field time ≥ half your round-trip drive; pick "Any" for long hauls.</div>
    <label class="toggle"><input type="checkbox" id="lowc"> <span data-i18n="lowc">♻︎ Prefer low-carbon trips</span></label>
    <div style="color:var(--mut);font-size:11px;margin:0 0 5px" data-i18n="lowc_hint">Rank by impact per kg of travel CO₂.</div>
    <label class="toggle"><input type="checkbox" id="startProsp"> <span data-i18n="startprosp">🔭 Also show species around my start</span></label>
  </details>
  <button id="plan" data-i18n="plan_trip">Plan my trip →</button>
  <div id="trips"></div>
  </div>
  <div id="prospects" data-idle="1"><div class="hd" style="margin-top:10px" data-i18n="prospects_idle">🔭 Tap a cell (or plan a trip) to see what to record there.</div></div>

  <details class="adv"><summary data-i18n="map_style">Map style</summary>
    <select id="basemap" class="full" style="margin-top:4px"></select>
    <div id="layertoggles" style="display:none;margin-top:8px">
      <label class="toggle" style="margin:4px 0"><input type="checkbox" id="tgRoads" checked> <span data-i18n="roads">Roads</span></label>
      <label class="toggle" style="margin:4px 0"><input type="checkbox" id="tgLabels" checked> <span data-i18n="labels_places">Labels &amp; places</span></label>
      <div style="color:var(--mut);font-size:11px;line-height:1.4" data-i18n="vector_hint">Vector basemap — toggle layers like Maputnik. Other styles are raster (roads baked in).</div>
    </div>
    <label class="toggle" style="margin:8px 0 2px"><input type="checkbox" id="tgCoverage"> <span data-i18n="inat_coverage">🛰 iNaturalist coverage</span></label>
    <div style="color:var(--mut);font-size:11px;line-height:1.4" data-i18n="coverage_hint">Where data already is (bright = well-sampled, dark = gaps) — the official "light up the map" layer, for the current group.</div>
    <div style="display:flex;justify-content:space-between;margin:9px 0 0"><span style="font-size:13px" data-i18n="map_brightness">Map brightness</span><span class="v" id="bopv" style="color:var(--acc)">100%</span></div>
    <input type="range" id="baseop" min="0.25" max="1" step="0.05" value="1" aria-label="Map brightness">
  </details>

  <details class="adv"><summary data-i18n="how_scored">How impact is scored & data sources</summary>
    <div class="legend" style="margin-top:8px"><span data-i18n="skip">skip</span><div class="bar"></div><span data-i18n="go_here">go here</span></div>
    <div style="color:var(--mut);font-size:11px;line-height:1.45;margin-top:7px" data-i18n-html="impact_expl">Each cell's <b style="color:var(--ink)">impact 0–100</b> is your goal mix, relative to the best cell shown. Hover a cell to see which goals drive it.</div>
    <p class="foot" data-i18n-html="foot"><b style="color:var(--ink)">How it works:</b> <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">Blitz the Gap</a> is a Canada-wide bioblitz — head to a high-priority spot, record what you see on <a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a>, and your research-grade sightings flow into the <a href="https://www.inaturalist.org/projects/blitz-the-gap-2026-general" target="_blank" rel="noopener" style="color:var(--acc)">2026 project</a>, filling the map's gaps.<br><br>Nationally, the robust priority signal is <b style="color:var(--ink)">under-sampling</b> (iNaturalist density) + <b style="color:var(--ink)">climate coverage</b> (CHELSA); rarity, freshness and forest-loss urgency are approximate placeholders being improved. Drive/cycle/walk routes from OSRM (FOSSGIS); travel time from Weiss 2018. Driving CO₂ ≈ 0.18 kg/km; cycling/walking zero. A planning aid — obscure sensitive-species locations and respect Indigenous data-sovereignty before any public release.<br><br>This map spans many <b style="color:var(--ink)">Indigenous territories</b> — see whose at <a href="https://native-land.ca" target="_blank" rel="noopener" style="color:var(--acc)">native-land.ca</a>, and seek consent before recording on their lands.</p>
  </details>
  <details class="adv" ontoggle="renderCellTable()"><summary data-i18n="top_cells">♿ Top cells (accessible list)</summary><div id="celltable"></div></details>
</div>
<div id="map"></div>
<div id="viewtoggle"><button id="vexplore" class="on" data-i18n="view_explore">🗺 Explore</button><button id="vplan" data-i18n="view_plan">🧭 Plan a trip</button><button id="vcompare" data-i18n="view_compare">📊 Compare goals</button></div>
<div id="loading" data-i18n="loading">🍃 Loading the map…</div>
<div id="maplegend"><div class="lt" data-i18n="legend_title">Where to blitz</div><div class="ramp"></div><div class="lab"><span data-i18n="legend_low">well-sampled</span><span data-i18n="legend_high">biggest gaps</span></div><div class="hint" data-i18n-html="legend_hint">Darker = higher priority. <span id="legendtap">Tap a cell to see what to record.</span></div></div>
<div id="insights"></div>
</div>

<script>
const FILES=__FILES__, OBJ=__OBJ__, PRESETS=__PRESETS__, DEFAULT=__DEFAULT__;

// ---- i18n (EN / Canadian French) ----------------------------------------
// Static UI chrome is keyed by string id and applied to [data-i18n*] nodes.
// Dynamic strings (trips, prospects, popups) call t('id', {vars}) at build time.
// OBJ/PRESET/group names below are indexed to keep the data structures stable.
const I18N={
  en:{
    title_full:`Where to <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--gd);text-decoration:underline">Blitz the Gap</a>`,
    sub:"Choose what counts as <b>impact</b>, then <b>explore</b> the priority map — or <b>plan a trip</b> to the best spot you can reach and get back from.",
    sec_group:"Life group & goal",
    info_btn:"Where do these scores come from?",
    taxinfo:`<b>Where the scores come from.</b> Canada-wide, on a 0.25° (~25&nbsp;km) grid.
      <ul>
      <li><b>Nationally the real signals are <span style="color:var(--ink)">under-sampling</span> (“Discover the most species”), <span style="color:var(--ink)">climate-coverage</span> (“Cover every habitat”) and <span style="color:var(--ink)">recent forest loss</span> (“Sample before it’s lost”).</b> Computed from real data (<a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a> density + CHELSA climate + Hansen forest loss); travel time is real too.</li>
      <li><b>Find rare species and Freshest gaps are approximate / being improved</b> — there’s no national rarity layer or per-record dates yet, so those are placeholders (conservation ≈ 0; staleness ≈ inverse density).</li>
      <li>The original <b>B.C. pilot had all five axes from real iNat history</b>; the national rollout has four of five real and is filling in the last (rare species).</li>
      <li><b>“All biodiversity”</b> averages 7 life groups (amphibians, birds, fungi, insects, mammals, plants, reptiles).</li>
      </ul>
      A planning aid, not ground truth — please obscure sensitive species and respect Indigenous data sovereignty. <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">How Blitz the Gap works →</a>`,
    finetune:"Fine-tune the five goals",
    your_trip:"Your trip",
    search_ph:"🔍 Search a town or address",
    my_location:"📍 My location",
    vancouver:"Vancouver",
    start_hint:`Start: <b id="startlbl">Vancouver</b> · <b style="color:var(--gd)">tap the map</b> to move it.`,
    time:"Time",
    unit_minutes:"Minutes", unit_hours:"Hours", unit_days:"Days",
    more_options:"More options",
    max_travel:"Max travel each way",
    no_limit:"No limit",
    worth_drive:"Worth the drive",
    ratio_any:"Any trip",
    ratio_half:"Record ≥ ½ the round trip",
    ratio_one:"Record ≥ the round trip",
    ratio_two:"Record ≥ 2× the round trip",
    worth_hint:`Drops mostly-driving trips. <b style="color:var(--ink)">Round trip = there and back</b> (both legs counted). Default: field time ≥ half your round-trip drive; pick "Any" for long hauls.`,
    lowc:"♻︎ Prefer low-carbon trips",
    lowc_hint:"Rank by impact per kg of travel CO₂.",
    startprosp:"🔭 Also show species around my start",
    plan_trip:"Plan my trip →",
    prospects_idle:"🔭 Tap a cell (or plan a trip) to see what to record there.",
    map_style:"Map style",
    roads:"Roads", labels_places:"Labels & places",
    vector_hint:"Vector basemap — toggle layers like Maputnik. Other styles are raster (roads baked in).",
    inat_coverage:"🛰 iNaturalist coverage",
    coverage_hint:`Where data already is (bright = well-sampled, dark = gaps) — the official "light up the map" layer, for the current group.`,
    map_brightness:"Map brightness",
    how_scored:"How impact is scored & data sources",
    skip:"skip", go_here:"go here",
    impact_expl:`Each cell's <b style="color:var(--ink)">impact 0–100</b> is your goal mix, relative to the best cell shown. Hover a cell to see which goals drive it.`,
    foot:`<b style="color:var(--ink)">How it works:</b> <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">Blitz the Gap</a> is a Canada-wide bioblitz — head to a high-priority spot, record what you see on <a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a>, and your research-grade sightings flow into the <a href="https://www.inaturalist.org/projects/blitz-the-gap-2026-general" target="_blank" rel="noopener" style="color:var(--acc)">2026 project</a>, filling the map's gaps.<br><br>Nationally, the robust priority signal is <b style="color:var(--ink)">under-sampling</b> (iNaturalist density) + <b style="color:var(--ink)">climate coverage</b> (CHELSA); rarity, freshness and forest-loss urgency are approximate placeholders being improved. Drive/cycle/walk routes from OSRM (FOSSGIS); travel time from Weiss 2018. Driving CO₂ ≈ 0.18 kg/km; cycling/walking zero. A planning aid — obscure sensitive-species locations and respect Indigenous data-sovereignty before any public release.<br><br>This map spans many <b style="color:var(--ink)">Indigenous territories</b> — see whose at <a href="https://native-land.ca" target="_blank" rel="noopener" style="color:var(--acc)">native-land.ca</a>, and seek consent before recording on their lands.`,
    top_cells:"♿ Top cells (accessible list)",
    view_explore:"🗺 Explore", view_plan:"🧭 Plan a trip", view_compare:"📊 Compare goals",
    loading:"🍃 Loading the map…",
    legend_title:"Where to blitz", legend_low:"well-sampled", legend_high:"biggest gaps",
    legend_hint:`Darker = higher priority. <span id="legendtap">Tap a cell to see what to record.</span>`,
    // dynamic
    legendtap_explore:"Tap a cell to see what to record.",
    legendtap_plan:"Tap the map to set your start.",
    near:"near", here:"here", my_loc_short:"my location", my_area_ip:"my area (from IP)",
    locating:"locating…", loc_unavail:"location unavailable",
    cells_loading:t=>`🔭 Loading ${t}…`,
    prospects_where_start:"Around your start", pop_go_title2:"Your destination",
    prospects_lookup:"🔭 Looking up what lives here…",
    prospects_none:"🔭 No research-grade records here yet — you could be the first to document what lives here.",
    prospects_err:"🔭 Couldn’t load species for this spot.",
    prospects_hd:(where,n,nearby)=>`🔭 <b style="color:var(--ink)">${where}</b> · ${n} species recorded. Keep an eye out for${nearby?' (✦ = nearby)':''}:`,
    rare:"rare", uncommon:"uncommon", gap:"gap",
    here_count:n=>`${n} here`, nearby_lbl:"nearby",
    worldwide:n=>`${n} worldwide`,
    explore_all:"Explore all on iNaturalist →", log_sighting:"＋ Log a sighting", for_challenge:"for this challenge",
    fill_gap_surveyed:`🎯 <b style="color:var(--ink)">Fill the gap</b> — common nearby, missing from this well-recorded cell:`,
    fill_gap_under:`🎯 <b style="color:var(--ink)">Undersampled here</b> — barely recorded; any of these (common nearby) helps:`,
    nearby_count:n=>`${n} nearby`,
    gap_popup:"🎯 Fill the gap — log these here:",
    join:"join →",
    finding_routes:m=>`Finding real ${m} routes…`,
    best_trips:(bud,lowc,est)=>`Best trips within ${bud} — most <b style="color:var(--ink)">impact × time you'd get to record</b>${lowc?', per kg CO₂':''}${est?' (some times estimated)':''}:`,
    or_skip:"Or skip the drive:",
    no_fit:(bud,mode)=>`No round trip fits ${bud} by ${mode} from here. The best move is to record where you are — or go farther with more time:`,
    farther:(bud,car)=>`Farther afield — over ${bud} round trip, but yours with more time${car?' or by 🚗':''}:`,
    right_here:"Right where you are",
    each_way:"each way", field:"field", in_field:"in the field", round:"round", over:"over",
    no_extra:"📍 no extra travel — record the cell you're already in",
    pop_here_title:"Record right where you are",
    pop_here_sub:"you're in this ~25 km cell — no extra travel needed",
    pop_here_foot:"📍 spend your time recording, not driving here",
    pop_go_title:"Go to this area", pop_go_sub:"— anywhere in the highlighted ~25 km cell",
    pop_centre:"centre", pop_impact:"impact",
    pop_round:"round trip", pop_estimated:"(estimated)",
    pop_over_budget:bud=>`round trip — over your ${bud}`,
    pop_explore_title:"This area", pop_explore_sub:"— anywhere in the highlighted ~25 km cell",
    share_link:"🔗 Copy link to this spot", copied:"✓ Copied!",
    car_free:"car-free 🌿",
    table_caption:"Top 40 cells for your goal mix & group, highest first. Tap a row to open it.",
    table_rank:"#", table_latlon:"Lat, lon", table_score:"Score",
    ins_hd:`<b>The same place — different goals, different life groups.</b> Each map shades every Canadian cell by one goal (<b>darker = go there</b>). The hot zones shift between goals (a value choice) and between groups (different species fill different gaps). Pick the rows & columns; tap any map to open it in the planner.`,
    ins_groups:"Groups (rows)", ins_goals:"Goals (columns)",
    ins_onhold:`<b>Discover vs. find-rare-species</b> — the rare-species (conservation) layer isn't available nationally yet (it needs per-record species history), so this comparison is on hold. <b>Discover</b>, <b>climate-coverage</b> and <b>forest-loss</b> are the live national goals.`,
    ins_spearman:(dis,verdict)=>`<b>Discover vs. find-rare-species</b> — Spearman ρ between cell rankings (over cells with records; negative = opposite places): ${dis}. ${verdict}`,
    ins_v_all_diff:"The two goals point to <b>different places in every group shown</b> — chasing quantity is not the same as chasing rarity.",
    ins_v_agree:"For the groups shown, the two goals mostly <b>agree</b> here.",
    ins_v_some:(neg,n)=>`They point to different places in <b>${neg} of ${n}</b> groups shown — quantity and rarity often diverge, but not always.`,
    // OBJ / PRESET / group display names (indexed)
    obj_name:["Discover the most species","Find rare species","Cover every habitat","Freshest gaps","Sample before it's lost"],
    obj_q:["go where few people have looked","go where range-restricted species are — national layer in progress","go where the climate is under-sampled","go where no one has been lately — approximate nationally","go where forest cover was recently lost (logging, fire, dieback)"],
    preset_name:["Biodiversity impact","The Other 99%","Most Wanted","Too Hot to Handle","Climate Gap","Revisit the Past"],
    preset_blurb:["Under-sampling + climate gaps — the strongest signal nationwide.","Skip the busy 1% — record in Canada's under-sampled 99%.","Range-restricted, at-risk species — national rarity layer still in progress.","Climate-exposed species in the fastest-warming areas.","Visit under-sampled climate & habitat types.","Cells overlooked lately — staleness is approximate nationally."],
    group:{Amphibia:"Amphibians",Aves:"Birds",Insecta:"Insects",Mammalia:"Mammals",Reptilia:"Reptiles",Plantae:"Plants",Fungi:"Fungi","All biodiversity":"All biodiversity"},
    modes:{Walk:"Walk",Cycle:"Cycle",Drive:"Drive"},
  },
  fr:{
    title_full:`Où aller pour <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--gd);text-decoration:underline">Blitz the Gap</a>`,
    sub:"Choisissez ce qui compte comme <b>impact</b>, puis <b>explorez</b> la carte des priorités — ou <b>planifiez une sortie</b> vers le meilleur endroit que vous pouvez atteindre et d'où vous pouvez revenir.",
    sec_group:"Groupe d'espèces et objectif",
    info_btn:"D'où viennent ces scores?",
    taxinfo:`<b>D'où viennent les scores.</b> À l'échelle du Canada, sur une grille de 0,25° (~25&nbsp;km).
      <ul>
      <li><b>À l'échelle nationale, les vrais signaux sont la <span style="color:var(--ink)">sous-représentation</span> (« Découvrir le plus d'espèces »), la <span style="color:var(--ink)">couverture climatique</span> (« Couvrir chaque habitat ») et la <span style="color:var(--ink)">perte forestière récente</span> (« Échantillonner avant qu'il soit trop tard »).</b> Calculés à partir de données réelles (densité <a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a> + climat CHELSA + perte forestière Hansen); le temps de déplacement est réel aussi.</li>
      <li><b>Trouver des espèces rares et Lacunes les plus fraîches sont approximatifs / en cours d'amélioration</b> — il n'y a pas encore de couche nationale de rareté ni de dates par observation, donc ces axes sont provisoires (conservation ≈ 0; fraîcheur ≈ densité inverse).</li>
      <li>Le <b>projet pilote en C.-B. comportait les cinq axes tirés de l'historique iNaturalist</b>; le déploiement national en compte quatre sur cinq et complète le dernier (espèces rares).</li>
      <li><b>« Toute la biodiversité »</b> fait la moyenne de 7 groupes (amphibiens, oiseaux, champignons, insectes, mammifères, plantes, reptiles).</li>
      </ul>
      Un outil de planification, pas une vérité absolue — veuillez masquer les espèces sensibles et respecter la souveraineté des données autochtones. <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">Fonctionnement de Blitz the Gap →</a>`,
    finetune:"Ajuster les cinq objectifs",
    your_trip:"Votre sortie",
    search_ph:"🔍 Rechercher une ville ou une adresse",
    my_location:"📍 Ma position",
    vancouver:"Vancouver",
    start_hint:`Départ : <b id="startlbl">Vancouver</b> · <b style="color:var(--gd)">touchez la carte</b> pour le déplacer.`,
    time:"Durée",
    unit_minutes:"Minutes", unit_hours:"Heures", unit_days:"Jours",
    more_options:"Plus d'options",
    max_travel:"Déplacement max par trajet",
    no_limit:"Aucune limite",
    worth_drive:"Vaut le déplacement",
    ratio_any:"Toute sortie",
    ratio_half:"Observer ≥ ½ de l'aller-retour",
    ratio_one:"Observer ≥ l'aller-retour",
    ratio_two:"Observer ≥ 2× l'aller-retour",
    worth_hint:`Écarte les sorties surtout en voiture. <b style="color:var(--ink)">Aller-retour = aller et retour</b> (les deux trajets comptés). Par défaut : temps sur le terrain ≥ la moitié de votre aller-retour; choisissez « Toute sortie » pour les longs trajets.`,
    lowc:"♻︎ Préférer les sorties à faible carbone",
    lowc_hint:"Classer par impact par kg de CO₂ de déplacement.",
    startprosp:"🔭 Montrer aussi les espèces près de mon départ",
    plan_trip:"Planifier ma sortie →",
    prospects_idle:"🔭 Touchez une cellule (ou planifiez une sortie) pour voir quoi observer.",
    map_style:"Style de carte",
    roads:"Routes", labels_places:"Étiquettes et lieux",
    vector_hint:"Fond vectoriel — activez les couches comme dans Maputnik. Les autres styles sont matriciels (routes intégrées).",
    inat_coverage:"🛰 Couverture iNaturalist",
    coverage_hint:`Où les données existent déjà (clair = bien échantillonné, foncé = lacunes) — la couche officielle « illuminer la carte », pour le groupe actuel.`,
    map_brightness:"Luminosité de la carte",
    how_scored:"Calcul de l'impact et sources de données",
    skip:"à éviter", go_here:"y aller",
    impact_expl:`L'<b style="color:var(--ink)">impact 0–100</b> de chaque cellule est votre combinaison d'objectifs, par rapport à la meilleure cellule affichée. Survolez une cellule pour voir les objectifs qui la motivent.`,
    foot:`<b style="color:var(--ink)">Fonctionnement :</b> <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">Blitz the Gap</a> est un bioblitz pancanadien — rendez-vous dans un endroit prioritaire, notez ce que vous voyez sur <a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a>, et vos observations de qualité recherche alimentent le <a href="https://www.inaturalist.org/projects/blitz-the-gap-2026-general" target="_blank" rel="noopener" style="color:var(--acc)">projet 2026</a>, comblant les lacunes de la carte.<br><br>À l'échelle nationale, le signal de priorité robuste est la <b style="color:var(--ink)">sous-représentation</b> (densité iNaturalist) + la <b style="color:var(--ink)">couverture climatique</b> (CHELSA); la rareté, la fraîcheur et l'urgence liée à la perte forestière sont des valeurs provisoires en cours d'amélioration. Itinéraires auto/vélo/marche d'OSRM (FOSSGIS); temps de déplacement de Weiss 2018. CO₂ en voiture ≈ 0,18 kg/km; vélo/marche nul. Un outil de planification — masquez les lieux d'espèces sensibles et respectez la souveraineté des données autochtones avant toute diffusion publique.<br><br>Cette carte couvre de nombreux <b style="color:var(--ink)">territoires autochtones</b> — voyez lesquels sur <a href="https://native-land.ca" target="_blank" rel="noopener" style="color:var(--acc)">native-land.ca</a>, et obtenez le consentement avant d'observer sur leurs terres.`,
    top_cells:"♿ Meilleures cellules (liste accessible)",
    view_explore:"🗺 Explorer", view_plan:"🧭 Planifier une sortie", view_compare:"📊 Comparer les objectifs",
    loading:"🍃 Chargement de la carte…",
    legend_title:"Où aller", legend_low:"bien échantillonné", legend_high:"plus grandes lacunes",
    legend_hint:`Plus foncé = priorité plus élevée. <span id="legendtap">Touchez une cellule pour voir quoi observer.</span>`,
    // dynamic
    legendtap_explore:"Touchez une cellule pour voir quoi observer.",
    legendtap_plan:"Touchez la carte pour définir votre départ.",
    near:"près de", here:"ici", my_loc_short:"ma position", my_area_ip:"ma région (selon l'IP)",
    locating:"localisation…", loc_unavail:"position indisponible",
    cells_loading:t=>`🔭 Chargement de ${t}…`,
    prospects_where_start:"Près de votre départ", pop_go_title2:"Votre destination",
    prospects_lookup:"🔭 Recherche de ce qui vit ici…",
    prospects_none:"🔭 Aucune observation de qualité recherche ici pour l'instant — vous pourriez être la première personne à documenter ce qui vit ici.",
    prospects_err:"🔭 Impossible de charger les espèces pour cet endroit.",
    prospects_hd:(where,n,nearby)=>`🔭 <b style="color:var(--ink)">${where}</b> · ${n} espèces observées. À surveiller${nearby?' (✦ = à proximité)':''} :`,
    rare:"rare", uncommon:"peu commun", gap:"lacune",
    here_count:n=>`${n} ici`, nearby_lbl:"à proximité",
    worldwide:n=>`${n} dans le monde`,
    explore_all:"Tout explorer sur iNaturalist →", log_sighting:"＋ Noter une observation", for_challenge:"pour ce défi",
    fill_gap_surveyed:`🎯 <b style="color:var(--ink)">Combler la lacune</b> — commun à proximité, absent de cette cellule bien documentée :`,
    fill_gap_under:`🎯 <b style="color:var(--ink)">Sous-échantillonné ici</b> — peu documenté; chacune de celles-ci (commune à proximité) aide :`,
    nearby_count:n=>`${n} à proximité`,
    gap_popup:"🎯 Combler la lacune — notez-les ici :",
    join:"se joindre →",
    finding_routes:m=>`Recherche d'itinéraires ${m} réels…`,
    best_trips:(bud,lowc,est)=>`Meilleures sorties en ${bud} — le plus d'<b style="color:var(--ink)">impact × temps pour observer</b>${lowc?', par kg de CO₂':''}${est?' (certains temps estimés)':''} :`,
    or_skip:"Ou évitez le déplacement :",
    no_fit:(bud,mode)=>`Aucun aller-retour ne tient en ${bud} en ${mode} d'ici. Le mieux est d'observer là où vous êtes — ou d'aller plus loin avec plus de temps :`,
    farther:(bud,car)=>`Plus loin — au-delà de ${bud} d'aller-retour, mais à votre portée avec plus de temps${car?' ou en 🚗':''} :`,
    right_here:"Là où vous êtes",
    each_way:"par trajet", field:"terrain", in_field:"sur le terrain", round:"aller-retour", over:"au-delà de",
    no_extra:"📍 aucun déplacement — observez la cellule où vous êtes déjà",
    pop_here_title:"Observez là où vous êtes",
    pop_here_sub:"vous êtes dans cette cellule de ~25 km — aucun déplacement requis",
    pop_here_foot:"📍 consacrez votre temps à observer, pas à conduire jusqu'ici",
    pop_go_title:"Allez dans cette zone", pop_go_sub:"— n'importe où dans la cellule de ~25 km en surbrillance",
    pop_centre:"centre", pop_impact:"impact",
    pop_round:"aller-retour", pop_estimated:"(estimé)",
    pop_over_budget:bud=>`aller-retour — au-delà de votre ${bud}`,
    pop_explore_title:"Cette zone", pop_explore_sub:"— n'importe où dans la cellule de ~25 km en surbrillance",
    share_link:"🔗 Copier le lien", copied:"✓ Copié !",
    car_free:"sans voiture 🌿",
    table_caption:"40 meilleures cellules pour votre combinaison d'objectifs et votre groupe, du plus élevé au plus bas. Touchez une ligne pour l'ouvrir.",
    table_rank:"#", table_latlon:"Lat, lon", table_score:"Score",
    ins_hd:`<b>Le même lieu — objectifs différents, groupes différents.</b> Chaque carte teinte chaque cellule canadienne selon un objectif (<b>plus foncé = y aller</b>). Les zones chaudes changent selon l'objectif (un choix de valeurs) et selon le groupe (des espèces différentes comblent des lacunes différentes). Choisissez les lignes et colonnes; touchez une carte pour l'ouvrir dans le planificateur.`,
    ins_groups:"Groupes (lignes)", ins_goals:"Objectifs (colonnes)",
    ins_onhold:`<b>Découvrir vs trouver des espèces rares</b> — la couche des espèces rares (conservation) n'est pas encore disponible à l'échelle nationale (elle nécessite l'historique par observation), donc cette comparaison est en attente. <b>Découvrir</b>, <b>couverture climatique</b> et <b>perte forestière</b> sont les objectifs nationaux actifs.`,
    ins_spearman:(dis,verdict)=>`<b>Découvrir vs trouver des espèces rares</b> — ρ de Spearman entre les classements des cellules (sur les cellules avec observations; négatif = lieux opposés) : ${dis}. ${verdict}`,
    ins_v_all_diff:"Les deux objectifs pointent vers des <b>lieux différents dans chaque groupe affiché</b> — chercher la quantité n'est pas chercher la rareté.",
    ins_v_agree:"Pour les groupes affichés, les deux objectifs <b>concordent</b> surtout ici.",
    ins_v_some:(neg,n)=>`Ils pointent vers des lieux différents dans <b>${neg} des ${n}</b> groupes affichés — quantité et rareté divergent souvent, mais pas toujours.`,
    obj_name:["Découvrir le plus d'espèces","Trouver des espèces rares","Couvrir chaque habitat","Lacunes les plus fraîches","Échantillonner avant qu'il soit trop tard"],
    obj_q:["allez où peu de gens ont cherché","allez où se trouvent les espèces à aire restreinte — couche nationale en cours","allez où le climat est sous-échantillonné","allez où personne n'est allé récemment — approximatif à l'échelle nationale","allez où le couvert forestier a été récemment perdu (coupe, feu, dépérissement)"],
    preset_name:["Impact sur la biodiversité","Les autres 99 %","Les plus recherchées","Trop chaud pour durer","Lacune climatique","Revisiter le passé"],
    preset_blurb:["Sous-représentation + lacunes climatiques — le signal le plus fort au pays.","Évitez le 1 % achalandé — observez dans le 99 % sous-échantillonné du Canada.","Espèces à aire restreinte et en péril — couche nationale de rareté encore en cours.","Espèces exposées au climat dans les zones qui se réchauffent le plus vite.","Visitez les types de climat et d'habitat sous-échantillonnés.","Cellules négligées récemment — la fraîcheur est approximative à l'échelle nationale."],
    group:{Amphibia:"Amphibiens",Aves:"Oiseaux",Insecta:"Insectes",Mammalia:"Mammifères",Reptilia:"Reptiles",Plantae:"Plantes",Fungi:"Champignons","All biodiversity":"Toute la biodiversité"},
    modes:{Walk:"Marche",Cycle:"Vélo",Drive:"Voiture"},
  }
};
let LANG=(()=>{try{const s=localStorage.getItem('wtb_lang');if(s==='en'||s==='fr')return s;}catch(e){}
  return (navigator.language||'en').toLowerCase().startsWith('fr')?'fr':'en';})();
function t(id,...a){const v=(I18N[LANG]&&I18N[LANG][id])??(I18N.en[id]);return typeof v==='function'?v(...a):v;}
// indexed-name helpers so the data structures (keys/weights/projects) stay English-stable
function objName(i){return t('obj_name')[i];}
function objQ(i){return t('obj_q')[i];}
function presetName(i){return t('preset_name')[i];}
function presetBlurb(i){return t('preset_blurb')[i];}
function groupName(k){return t('group')[k]||k;}
function modeName(k){return t('modes')[k]||k;}
function applyI18N(){
  document.documentElement.lang=LANG;   // WCAG 3.1.1
  // preserve a user-moved start label across re-paints of the start_hint block
  const slPrev=document.getElementById('startlbl');const slTxt=slPrev?slPrev.textContent:null;
  document.querySelectorAll('[data-i18n]').forEach(el=>{const v=t(el.getAttribute('data-i18n'));if(v!=null)el.textContent=v;});
  document.querySelectorAll('[data-i18n-html]').forEach(el=>{const v=t(el.getAttribute('data-i18n-html'));if(v!=null)el.innerHTML=v;});
  if(slTxt!=null&&slTxt!=='Vancouver'){const sl=document.getElementById('startlbl');if(sl)sl.textContent=slTxt;}
  document.querySelectorAll('[data-i18n-ph]').forEach(el=>{const v=t(el.getAttribute('data-i18n-ph'));if(v!=null)el.placeholder=v;});
  document.querySelectorAll('[data-i18n-title]').forEach(el=>{const v=t(el.getAttribute('data-i18n-title'));if(v!=null)el.title=v;});
  const le=document.getElementById('lang-en'),lf=document.getElementById('lang-fr');
  if(le)le.classList.toggle('on',LANG==='en');if(lf)lf.classList.toggle('on',LANG==='fr');
}
function relabelDynamic(){
  // re-render the JS-built widgets whose static strings come from the dict
  if(typeof rebuildTaxonOptions==='function')rebuildTaxonOptions();
  if(typeof rebuildObjs==='function')rebuildObjs();
  if(typeof rebuildPresets==='function')rebuildPresets();
  if(typeof rebuildModes==='function')rebuildModes();
  // active challenge blurb + project link
  if(typeof window.__activePreset==='number')applyChallenge(PRESETS[window.__activePreset]);
  // budget label unit suffix
  if(typeof refreshBudget==='function')refreshBudget();
  // legend "tap" hint follows current view
  const lt=document.getElementById('legendtap');if(lt)lt.textContent=t(state&&state.view==='plan'?'legendtap_plan':'legendtap_explore');
  // start-place prospects idle text (only if untouched)
  const pr=document.getElementById('prospects');
  if(pr&&pr.dataset.idle==='1')pr.innerHTML='<div class="hd" style="margin-top:10px" data-i18n="prospects_idle">'+t('prospects_idle')+'</div>';
}
function setLang(l){if(l!==LANG){LANG=l;try{localStorage.setItem('wtb_lang',l);}catch(e){}}
  applyI18N();relabelDynamic();}

const DATA={}, DATA_DIR='cluster_results/ca/';
async function loadGroup(g){if(DATA[g])return;const r=await fetch(DATA_DIR+FILES[g]);Object.assign(DATA,await r.json());}
const IDX={discover:2,conservation:3,env:4,staleness:5,urgency:6}, TT=7, NTR=8;
const OSRM_BASE="https://routing.openstreetmap.de/";   // FOSSGIS public OSRM (car/bike/foot, CORS-enabled)
const MODES={Walk:{host:'routed-foot',kmh:5,emit:0,icon:'🚶'},Cycle:{host:'routed-bike',kmh:14,emit:0,icon:'🚲'},Drive:{host:'routed-car',kmh:60,emit:0.18,icon:'🚗'}};
const UNITS={Minutes:{toH:1/60,min:15,max:600,step:15,def:120},Hours:{toH:1,min:1,max:14,step:0.5,def:5},Days:{toH:24,min:1,max:21,step:1,def:2}};
const ROAD_FACTOR=1.35, MIN_FIELD_H=0.5, N_CANDIDATES=8;
function co2lbl(kg){return kg<=0?t('car_free'):'~'+(kg<10?kg.toFixed(1):Math.round(kg))+' kg CO₂';}
const ICONIC={Amphibia:'Amphibia',Aves:'Aves',Insecta:'Insecta',Mammalia:'Mammalia',Reptilia:'Reptilia',Plantae:'Plantae',Fungi:'Fungi'};
let prospectSeq=0;
// whereKey is a stable sentinel ('around_start'|'right_here'|'destination'); the displayed
// heading is translated from it so the gap-popup test stays language-independent.
const WHERE_LBL={around_start:'prospects_where_start',right_here:'right_here',destination:'pop_go_title2'};
async function fetchProspects(lat,lon,whereKey){
  const myseq=++prospectSeq;
  const where=t(WHERE_LBL[whereKey]||'here');
  const pr=document.getElementById('prospects'); pr.dataset.idle='0'; pr.innerHTML='<div class="hd">'+t('prospects_lookup')+'</div>';
  // labels needed inside taxon-shadowed (.map(r=>{const t=r.taxon...})) closures
  const L_rare=t('rare'),L_unc=t('uncommon'),L_gap=t('gap'),L_nearby=t('nearby_lbl');
  const T_here=n=>t('here_count',n),T_world=g=>t('worldwide',g.toLocaleString(LANG==='fr'?'fr-CA':'en-CA')),T_nb=n=>t('nearby_count',n);
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
    if(!res.length){pr.innerHTML='<div class="hd">'+t('prospects_none')+'</div>';return;}
    res=res.filter(r=>(r.taxon.observations_count||0)>40);                         // drop unverifiable one-offs
    res.sort((a,b)=>(a.taxon.observations_count||1e12)-(b.taxon.observations_count||1e12)); // globally rarest, but present here
    res=res.slice(0,6);
    const ex=`https://www.inaturalist.org/observations?subview=map&swlat=${lat-HH}&nelat=${lat+HH}&swlng=${lon-HH}&nelng=${lon+HH}&quality_grade=research`;
    pr.innerHTML=`<div class="hd">${t('prospects_hd','<b style="color:var(--ink)">'+(where||t('here'))+'</b>',total.toLocaleString(LANG==='fr'?'fr-CA':'en-CA'),nearby)}</div>`+
      '<div class="prospects">'+res.map(r=>{const tx=r.taxon,g=tx.observations_count||0,rare=g<1500,unc=g<7000;
        return `<a class="sp" href="https://www.inaturalist.org/taxa/${tx.id}" target="_blank" rel="noopener" title="${tx.name}"><img src="${tx.default_photo.square_url}" loading="lazy" alt=""><div class="nm">${r._here?'':'✦ '}${tx.preferred_common_name||tx.name}${rare?` <span class="rare">${L_rare}</span>`:(unc?` <span class="unc">${L_unc}</span>`:'')}</div><div class="ct">${r._here?T_here(r.count):L_nearby} · ${T_world(g)}</div></a>`;}).join('')+'</div>'+'<div id="firsts"></div>'+
      `<div style="margin-top:7px;font-size:11.5px"><a href="${ex}" target="_blank" rel="noopener" style="color:var(--acc)">${t('explore_all')}</a> &nbsp;·&nbsp; <a href="https://www.inaturalist.org/observations/new" target="_blank" rel="noopener" style="color:var(--gd)">${t('log_sighting')}</a> &nbsp;·&nbsp; <a href="https://www.inaturalist.org/projects/${state.project}" target="_blank" rel="noopener" style="color:var(--mut)">${t('for_challenge')}</a></div>`;
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
      fe.innerHTML=`<div class="hd" style="margin-top:11px">${surveyed?t('fill_gap_surveyed'):t('fill_gap_under')}</div>`+
        '<div class="prospects">'+firsts.map(r=>{const tx=r.taxon,nm=tx.preferred_common_name||tx.name;return `<a class="sp" href="https://www.inaturalist.org/taxa/${tx.id}" target="_blank" rel="noopener" title="${tx.name}"><img src="${tx.default_photo.square_url}" loading="lazy" alt="${nm}"><div class="nm">${nm} <span class="first">${L_gap}</span></div><div class="ct">${T_nb(r.count.toLocaleString(LANG==='fr'?'fr-CA':'en-CA'))}</div></a>`;}).join('')+'</div>';
    }
    // surface the actionable gap right on the map popup -- no panel scroll needed
    if(whereKey==='destination' && destMarker && destMarker.getPopup() && firsts.length){
      const gapThumbs='<div style="font-size:11.5px;color:#1b7837;font-weight:700;margin:9px 0 4px">'+t('gap_popup')+'</div><div style="display:flex;gap:6px">'+firsts.slice(0,4).map(r=>{const tx=r.taxon,nm=tx.preferred_common_name||tx.name;return `<a href="https://www.inaturalist.org/taxa/${tx.id}" target="_blank" rel="noopener" style="width:62px;text-decoration:none;color:#0a2a44" title="${tx.name}"><img src="${tx.default_photo.square_url}" style="width:62px;height:62px;object-fit:cover;border-radius:7px;border:1px solid #cdd;display:block"><div style="font-size:10px;line-height:1.15;margin-top:3px;height:24px;overflow:hidden">${nm}</div></a>`;}).join('')+'</div>';
      destMarker.setPopupContent(destMarker.getPopup().getContent()+gapThumbs); destMarker.openPopup();
    }
  }catch(e){pr.innerHTML='<div class="hd">'+t('prospects_err')+'</div>';}
}
async function fetchFirsts(lat,lon,ic,cellIds){
  const R=0.5;   // ~50 km neighbourhood -- same habitat zone, not a different ecoregion
  const u=`https://api.inaturalist.org/v1/observations/species_counts?swlat=${lat-R}&nelat=${lat+R}&swlng=${lon-R}&nelng=${lon+R}&quality_grade=research&taxon_geoprivacy=open&threatened=false&per_page=200&order_by=count`+(ic?`&iconic_taxa=${ic}`:'');
  try{const j=await fetch(u).then(r=>r.json());
    return (j.results||[]).filter(r=>r.taxon&&r.taxon.default_photo&&!cellIds.has(r.taxon.id)&&r.count>=10&&(r.taxon.observations_count||0)>40).slice(0,6);
  }catch(e){return [];}
}
const showProspects=debounce((lat,lon)=>fetchProspects(lat,lon,'around_start'),500);

const RAMP=[[247,252,245],[229,245,224],[199,233,192],[161,217,155],[116,196,118],[65,171,93],[35,139,69],[0,109,44],[0,68,27]]; // Greens: pale=skip, deep green=GO (traffic-light intuitive, calm)
function colour(t){t=Math.max(0,Math.min(1,t));const x=t*(RAMP.length-1),i=Math.floor(x),f=x-i;const a=RAMP[i],b=RAMP[Math.min(i+1,RAMP.length-1)];return `rgb(${Math.round(a[0]+(b[0]-a[0])*f)},${Math.round(a[1]+(b[1]-a[1])*f)},${Math.round(a[2]+(b[2]-a[2])*f)})`;}
function fmth(h){if(h>=24){const d=Math.floor(h/24),hr=Math.round(h%24);return d+'d'+(hr?(' '+hr+'h'):'');}const m=Math.round(h*60);return m>=60?Math.floor(m/60)+'h'+String(m%60).padStart(2,'0'):m+' min';}
function haversine(a,b,c,d){const R=6371,r=Math.PI/180,x=(c-a)*r,y=(d-b)*r,h=Math.sin(x/2)**2+Math.cos(a*r)*Math.cos(c*r)*Math.sin(y/2)**2;return 2*R*Math.asin(Math.sqrt(h));}

const map=L.map('map',{zoomControl:true,preferCanvas:true}).setView([58,-96],4);
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
const state={taxon:(FILES["All biodiversity"]?"All biodiversity":Object.keys(FILES)[0]), w:w0, start:VAN.slice(), budget:5, maxLeg:0, minRatio:0.5, unit:'Hours', lowc:false, mode:'Drive', startProsp:false, view:'explore', project:'blitz-the-gap-2026-general'};
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
    .then(r=>r.json()).then(j=>{const a=j.address||{};const nm=a.city||a.town||a.village||a.hamlet||a.county||a.state||((j.display_name||'').split(',')[0])||t('here');
      setStartLabel((tag?tag+' · ':'')+t('near')+' '+nm);}).catch(()=>{});},600);

function rows(){return DATA[state.taxon]||[];}
function impact(r){let s=0;for(const o of OBJ)s+=state.w[o.key]*(r[IDX[o.key]]||0);return s;}
function contribs(r){return OBJ.map((o,i)=>({nm:objName(i),c:state.w[o.key]*(r[IDX[o.key]]||0),raw:r[IDX[o.key]]||0})).filter(x=>x.c>0).sort((a,b)=>b.c-a.c);}
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
  el.innerHTML='<table><caption>'+t('table_caption')+'</caption><thead><tr><th scope="col">'+t('table_rank')+'</th><th scope="col">'+t('table_latlon')+'</th><th scope="col">'+t('table_score')+'</th></tr></thead><tbody>'+
    top.map((o,i)=>`<tr tabindex="0" role="button" data-la="${o.r[0]}" data-lo="${o.r[1]}" aria-label="Rank ${i+1}: latitude ${o.r[0].toFixed(2)}, longitude ${o.r[1].toFixed(2)}, score ${(o.t*100|0)} of 100"><td>${i+1}</td><td>${o.r[0].toFixed(2)}, ${o.r[1].toFixed(2)}</td><td>${(o.t*100|0)}/100</td></tr>`).join('')+'</tbody></table>';
  el.querySelectorAll('tr[data-la]').forEach(tr=>{const go=()=>{const la=+tr.dataset.la,lo=+tr.dataset.lo;map.setView([la,lo],9);state.view==='plan'?setStart(la,lo):exploreCell(la,lo);};tr.onclick=go;tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}};});
}
function recolour(){
  const cov=document.getElementById('tgCoverage')&&document.getElementById('tgCoverage').checked;
  const vals=markers.map(m=>impact(m.r));
  // Percentile-rank, not min-max: a few Arctic super-gaps (max discover + rare climate)
  // otherwise dominate the scale and crush every reachable cell to ~0/100. Rank spreads it evenly.
  const ord=vals.map((v,i)=>[v,i]).sort((a,b)=>a[0]-b[0]);const rank=new Array(vals.length);ord.forEach((p,k)=>rank[p[1]]=k);const n1=Math.max(vals.length-1,1);
  // If the chosen goal mix has no spatial signal (e.g. only a degenerate/placeholder axis like
  // national conservation=0), rank would paint a fake index gradient -- show a flat map instead.
  const flat=ord.length>0&&(ord[ord.length-1][0]-ord[0][0])<1e-9;   // guard empty markers (recolour runs during init before data loads)
  // No per-cell tooltip: at ~38k cells binding one tooltip each tanks pan/zoom.
  // Cells stay clickable -- the click popup already shows the cell's score & drivers.
  markers.forEach((m,i)=>{const t=flat?0:rank[i]/n1;m.t=t;m.mk.setStyle({fillColor:colour(t),fillOpacity:cov?0:0.1+0.62*t});});
  renderCellTable();
}

// controls
const taxonSel=document.getElementById('taxon');
function rebuildTaxonOptions(){const cur=state.taxon;taxonSel.innerHTML='';
  Object.keys(FILES).forEach(k=>{const o=document.createElement('option');o.value=k;o.textContent=groupName(k);taxonSel.appendChild(o);});
  taxonSel.value=cur;}
rebuildTaxonOptions();
taxonSel.onchange=()=>{state.taxon=taxonSel.value;
  const pr=document.getElementById('prospects');if(pr){pr.dataset.idle='0';pr.innerHTML='<div class="hd">'+t('cells_loading',groupName(state.taxon))+'</div>';}
  if(typeof showLoading==='function')showLoading(true);
  loadGroup(state.taxon).then(()=>{buildMarkers();setCoverage();if(document.getElementById('insights').style.display==='block')renderInsights();if(typeof showLoading==='function')showLoading(false);});};

const objsDiv=document.getElementById('objs');
function rebuildObjs(){objsDiv.innerHTML='';
  OBJ.forEach((o,i)=>{const d=document.createElement('div');d.className='obj';
    d.innerHTML=`<div class="top"><span class="nm">${objName(i)}</span><span class="v" id="v_${o.key}">${state.w[o.key].toFixed(2)}</span></div>
      <div class="q">${objQ(i)}</div><input type="range" id="s_${o.key}" min="0" max="1" step="0.05" value="${state.w[o.key]}" aria-label="${objName(i)}">`;
    objsDiv.appendChild(d);
    d.querySelector('input').addEventListener('input',e=>{state.w[o.key]=parseFloat(e.target.value);
      document.getElementById('v_'+o.key).textContent=state.w[o.key].toFixed(2);markPreset(null);recolour();replan();});});}
rebuildObjs();
function applyWeights(arr,name){OBJ.forEach((o,i)=>{state.w[o.key]=arr[i];document.getElementById('s_'+o.key).value=arr[i];document.getElementById('v_'+o.key).textContent=arr[i].toFixed(2);});markPreset(name);recolour();replan();}
const presetsDiv=document.getElementById('presets');
function markPreset(name){[...presetsDiv.children].forEach(b=>b.classList.toggle('on',b.textContent===name));}
function rebuildPresets(){presetsDiv.innerHTML='';
  PRESETS.forEach((p,i)=>{const b=document.createElement('button');b.textContent=presetName(i);b.title=presetBlurb(i);b.onclick=()=>applyChallenge(p);presetsDiv.appendChild(b);});}
rebuildPresets();
function applyChallenge(p){const i=PRESETS.indexOf(p);window.__activePreset=i;
  applyWeights(p.w,presetName(i));state.project=p.proj;
  document.getElementById('challengeBlurb').innerHTML=presetBlurb(i)+' <a href="https://www.inaturalist.org/projects/'+p.proj+'" target="_blank" rel="noopener" style="color:var(--acc);white-space:nowrap">'+t('join')+'</a>';}
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
document.getElementById('tgCoverage').addEventListener('change',()=>{setCoverage();recolour();});
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
document.getElementById('startProsp').addEventListener('change',e=>{state.startProsp=e.target.checked; if(state.startProsp) fetchProspects(state.start[0],state.start[1],'around_start');});

const modesDiv=document.getElementById('modes');
function rebuildModes(){modesDiv.innerHTML='';
  Object.keys(MODES).forEach(mn=>{const b=document.createElement('button');b.textContent=MODES[mn].icon+' '+modeName(mn);b.dataset.m=mn;
    b.onclick=()=>{state.mode=mn;[...modesDiv.children].forEach(x=>x.classList.toggle('on',x.dataset.m===mn));replan();};
    modesDiv.appendChild(b);});
  [...modesDiv.children].forEach(x=>x.classList.toggle('on',x.dataset.m===state.mode));}
rebuildModes();

document.getElementById('setVan').onclick=()=>{map.panTo(VAN);setStart(VAN[0],VAN[1],'Vancouver');};
// type a town/address -> Nominatim forward geocoding (biased to BC/Canada), pick from a dropdown
const psEl=document.getElementById('placeSearch'), srEl=document.getElementById('searchResults');
function closeResults(){srEl.classList.remove('open');srEl.innerHTML='';}
const doSearch=debounce(async q=>{
  if(q.trim().length<3){closeResults();return;}
  try{
    const u=`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&countrycodes=ca&viewbox=-141,84,-52,41&q=${encodeURIComponent(q)}`;
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
document.getElementById('setMe').onclick=()=>{setStartLabel(t('locating'));
  const ok=(lat,lon,how)=>{map.panTo([lat,lon]);setStart(lat,lon,how);};
  let done=false;
  if(navigator.geolocation){navigator.geolocation.getCurrentPosition(p=>{done=true;ok(p.coords.latitude,p.coords.longitude,t('my_loc_short'));},()=>{if(!done)ipLoc(ok);},{timeout:6000});setTimeout(()=>{if(!done)ipLoc(ok);},6500);}else ipLoc(ok);};
function ipLoc(ok){fetch('https://ipapi.co/json/').then(r=>r.json()).then(j=>{if(j&&j.latitude)ok(j.latitude,j.longitude,t('my_area_ip'));else setStartLabel(t('loc_unavail'));}).catch(()=>setStartLabel(t('loc_unavail')));}

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
  trips.innerHTML='<div class="hd">'+t('finding_routes',modeName(state.mode).toLowerCase())+'</div>';
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
  const title=o.here?np+t('right_here'):np+o.m.r[0].toFixed(2)+', '+o.m.r[1].toFixed(2);
  const line2=o.here
    ?t('no_extra')
    :`${MODES[state.mode].icon} ${fmth(o.oneH)} ${t('each_way')} · ${o.fieldH>=MIN_FIELD_H?fmth(o.fieldH)+' '+t('field'):fmth(2*o.oneH)+' '+t('round')+' · '+t('over')+' '+budvEl.textContent} · ${co2lbl(o.co2)}`;
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
    el.innerHTML='<div class="hd">'+t('best_trips',budvEl.textContent,state.lowc,est)+'</div>';
    trips.slice(0,5).forEach((o,i)=>tripRow(el,o,i,i+1));
    subhd(el,t('or_skip'));tripRow(el,here,'here',0);
    selectTrip(trips[0]);
  } else {
    el.innerHTML='<div class="hd">'+t('no_fit',budvEl.textContent,modeName(state.mode).toLowerCase())+'</div>';
    tripRow(el,here,'here',1);
    const far=lastScored.slice().sort((a,b)=>a.oneH-b.oneH).slice(0,3);
    if(far.length){subhd(el,t('farther',budvEl.textContent,state.mode!=='Drive'));far.forEach((o,i)=>tripRow(el,o,'f'+i,0));}
    selectTrip(here);
  }
}
function clearRoute(){[routeLine,destMarker,destCell].forEach(l=>{if(l)map.removeLayer(l);});routeLine=destMarker=destCell=null;}
function selectTrip(o){clearRoute();const dest=[o.m.r[0],o.m.r[1]];
  destCell=L.rectangle([[dest[0]-0.125,dest[1]-0.125],[dest[0]+0.125,dest[1]+0.125]],{color:'#1b7837',weight:2,dashArray:'5 5',fillColor:'#74c476',fillOpacity:0.16,interactive:false}).addTo(map);
  if(o.here){
    map.fitBounds(destCell.getBounds(),{padding:[90,90],maxZoom:11});
    destMarker=L.marker(dest,{icon:destIcon,zIndexOffset:900}).addTo(map)
      .bindPopup(`<b>${t('pop_here_title')}</b><br><span style="color:#667">${t('pop_here_sub')}</span><br>${t('pop_impact')} <b>${(o.m.t*100|0)}/100</b> · ${contribStr(o.m.r)}<br>${t('pop_here_foot')}`).openPopup();
    document.querySelectorAll('#trips .row').forEach(el=>el.classList.toggle('sel',el.dataset.id===o.id));
    fetchProspects(dest[0],dest[1],'right_here');return;
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
    .bindPopup(`<b>${t('pop_go_title')}</b> <span style="color:#667">${t('pop_go_sub')}</span><br><span style="color:#667">${t('pop_centre')} ${dest[0].toFixed(2)}, ${dest[1].toFixed(2)}</span><br>${t('pop_impact')} <b>${(o.m.t*100|0)}/100</b> · ${contribStr(o.m.r)}<br>${o.fieldH>=MIN_FIELD_H?`${MODES[state.mode].icon} ${fmth(o.oneH)} ${t('each_way')} · ${fmth(o.fieldH)} ${t('in_field')}`:`${MODES[state.mode].icon} ${fmth(o.oneH)} ${t('each_way')} · ${fmth(2*o.oneH)} ${t('pop_over_budget',budvEl.textContent)}`}<br>${co2lbl(o.co2)} ${t('pop_round')}${o.real?'':' '+t('pop_estimated')}`).openPopup();
  document.querySelectorAll('#trips .row').forEach(el=>el.classList.toggle('sel',el.dataset.id===o.id));
  fetchProspects(o.m.r[0],o.m.r[1],'destination');
}

// ---- Insights view: the §2 figure, interactive ----
function rankArr(a){const idx=a.map((v,i)=>[v,i]).sort((x,y)=>x[0]-y[0]);const r=new Array(a.length);
  let i=0;while(i<idx.length){let j=i;while(j+1<idx.length&&idx[j+1][0]===idx[i][0])j++;const avg=(i+j)/2;for(let k=i;k<=j;k++)r[idx[k][1]]=avg;i=j+1;}  // average ranks for ties (proper Spearman; thousands of tied zeros otherwise distort it)
  return r;}
function spear(a,b){const ra=rankArr(a),rb=rankArr(b),n=a.length;let ma=(n-1)/2,num=0,da=0,db=0;for(let i=0;i<n;i++){const x=ra[i]-ma,y=rb[i]-ma;num+=x*y;da+=x*x;db+=y*y;}return da&&db?num/Math.sqrt(da*db):0;}
const BB={minlon:-141,maxlon:-52,minlat:41,maxlat:84};
function drawMini(cv,rows,gi){
  const ctx=cv.getContext('2d'),W=cv.width,H=cv.height;ctx.clearRect(0,0,W,H);
  const vals=rows.map(r=>r[2+gi]),lo=Math.min(...vals),hi=Math.max(...vals),rng=(hi-lo)||1;
  const cw=W/((BB.maxlon-BB.minlon)/0.25)+0.6,ch=H/((BB.maxlat-BB.minlat)/0.25)+0.6;
  for(const r of rows){const t=(r[2+gi]-lo)/rng;
    const x=(r[1]-BB.minlon)/(BB.maxlon-BB.minlon)*W,y=(1-(r[0]-BB.minlat)/(BB.maxlat-BB.minlat))*H;
    ctx.fillStyle=colour(t);ctx.globalAlpha=0.14+0.72*t;ctx.fillRect(x-cw/2,y-ch/2,cw,ch);}
  ctx.globalAlpha=1;
}
async function renderInsights(){
  const ins=document.getElementById('insights'), taxa=Object.keys(FILES);
  if(!state.insTaxa) state.insTaxa=taxa.includes('All biodiversity')?['All biodiversity']:[taxa[0]];   // comprehensive overview by default; add groups to compare across taxa
  if(!state.insGoals) state.insGoals=[0,1,2,3,4];
  const goalsOn=OBJ.map((o,i)=>i).filter(i=>state.insGoals.includes(i));
  const rowsTaxa=state.insTaxa.length?state.insTaxa:[taxa[0]];
  await Promise.all(rowsTaxa.map(loadGroup));   // fetch the groups we're about to draw/score
  let html='<div class="ihd">'+t('ins_hd')+'</div>';
  html+='<div class="ctrls"><div class="grp"><span class="lbl">'+t('ins_groups')+'</span>'+taxa.map(tx=>`<span class="chip ${state.insTaxa.includes(tx)?'on':''}" role="button" tabindex="0" aria-pressed="${state.insTaxa.includes(tx)}" data-tx="${tx}">${groupName(tx)}</span>`).join('')+'</div>';
  html+='<div class="grp"><span class="lbl">'+t('ins_goals')+'</span>'+OBJ.map((o,i)=>`<span class="chip ${state.insGoals.includes(i)?'on':''}" role="button" tabindex="0" aria-pressed="${state.insGoals.includes(i)}" data-gl="${i}">${objName(i)}</span>`).join('')+'</div></div>';
  html+=`<div class="matrix" style="grid-template-columns:100px repeat(${goalsOn.length},1fr)"><div></div>`;
  goalsOn.forEach(gi=>html+=`<div class="gh">${objName(gi)}<span class="gq">${objQ(gi)}</span></div>`);
  rowsTaxa.forEach(tx=>{html+=`<div class="rl">${groupName(tx)}</div>`;goalsOn.forEach(gi=>html+=`<div class="cell" data-tx="${tx}" data-gl="${gi}"><canvas width="220" height="170"></canvas></div>`);});
  html+='</div><div class="idis" id="idis"></div>';
  ins.innerHTML=html;
  ins.querySelectorAll('.cell').forEach(c=>{drawMini(c.querySelector('canvas'),DATA[c.dataset.tx],+c.dataset.gl);
    c.onclick=()=>{const tx=c.dataset.tx,gi=+c.dataset.gl;taxonSel.value=tx;state.taxon=tx;loadGroup(tx).then(()=>{buildMarkers();applyWeights(OBJ.map((_,i)=>i===gi?1:0),objName(gi));setView('plan');});};});
  ins.querySelectorAll('.chip[data-tx]').forEach(ch=>{ch.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();ch.click();}};ch.onclick=()=>{const t=ch.dataset.tx,i=state.insTaxa.indexOf(t);if(i>=0){if(state.insTaxa.length>1)state.insTaxa.splice(i,1);}else state.insTaxa.push(t);renderInsights();};});
  ins.querySelectorAll('.chip[data-gl]').forEach(ch=>{ch.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();ch.click();}};ch.onclick=()=>{const g=+ch.dataset.gl,i=state.insGoals.indexOf(g);if(i>=0){if(state.insGoals.length>1)state.insGoals.splice(i,1);}else state.insGoals.push(g);state.insGoals.sort((x,y)=>x-y);renderInsights();};});
  // If the conservation axis is degenerate (constant -- e.g. the national placeholder is 0
  // everywhere), Spearman is undefined and would falsely read as "they agree". Say so honestly.
  const consVaries=rowsTaxa.some(tx=>{const base=DATA[tx].filter(r=>r[8]>0),b=base.length>=20?base:DATA[tx];let mn=1e9,mx=-1e9;for(const r of b){if(r[3]<mn)mn=r[3];if(r[3]>mx)mx=r[3];}return mx>mn;});
  if(!consVaries){document.getElementById('idis').innerHTML=t('ins_onhold');return;}
  const disVals=rowsTaxa.map(tx=>{const base=DATA[tx].filter(r=>r[8]>0),b=base.length>=20?base:DATA[tx];return {tx,rho:spear(b.map(r=>r[2]),b.map(r=>r[3]))};});
  const dis=disVals.map(d=>`${groupName(d.tx)} <b>${d.rho.toFixed(2)}</b>`).join(' · ');
  const neg=disVals.filter(d=>d.rho<0).length, n=disVals.length;   // verdict follows the actual signs, not a hardcoded claim
  const verdict = neg===n ? t('ins_v_all_diff') : neg===0 ? t('ins_v_agree') : t('ins_v_some',neg,n);
  document.getElementById('idis').innerHTML=t('ins_spearman',dis,verdict);
}
// Explore mode: tap a cell to see its score + what to record there (no trip planning).
function exploreCell(lat,lon){
  let best=markers[0],bd=1e9;for(const m of markers){const d=Math.abs(m.r[0]-lat)+Math.abs(m.r[1]-lon);if(d<bd){bd=d;best=m;}}
  const o=best,dest=[o.r[0],o.r[1]];clearRoute();
  try{history.replaceState(null,'','?at='+dest[0].toFixed(3)+','+dest[1].toFixed(3)+'&g='+encodeURIComponent(state.taxon));}catch(e){}
  destCell=L.rectangle([[dest[0]-0.125,dest[1]-0.125],[dest[0]+0.125,dest[1]+0.125]],{color:'#1b7837',weight:2,dashArray:'5 5',fillColor:'#74c476',fillOpacity:0.16,interactive:false}).addTo(map);
  destMarker=L.marker(dest,{icon:destIcon,zIndexOffset:900}).addTo(map)
    .bindPopup(`<b>${t('pop_explore_title')}</b> ${t('pop_explore_sub')}<br><span style="color:#667">${t('pop_centre')} ${dest[0].toFixed(2)}, ${dest[1].toFixed(2)}</span><br>${t('pop_impact')} <b>${(o.t*100|0)}/100</b> · ${contribStr(o.r)}<br><a href="#" onclick="navigator.clipboard&&navigator.clipboard.writeText(location.href);this.textContent=t('copied');return false;" style="color:#1f6fe0;font-size:12px">${t('share_link')}</a>`).openPopup();
  fetchProspects(dest[0],dest[1],'destination');
}
function setView(v){
  state.view=v;
  document.getElementById('insights').style.display=v==='compare'?'block':'none';
  const ml=document.getElementById('maplegend');if(ml)ml.style.display=v==='compare'?'none':'block';
  const lt=document.getElementById('legendtap');if(lt)lt.textContent=t(v==='plan'?'legendtap_plan':'legendtap_explore');
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

function showLoading(on){const el=document.getElementById('loading');if(el)el.style.display=on?'block':'none';}

// language toggle: seed from navigator.language / localStorage (done above), wire buttons, apply chrome
document.getElementById('lang-en').onclick=()=>setLang('en');
document.getElementById('lang-fr').onclick=()=>setLang('fr');
applyI18N();   // paint static chrome + set <html lang> for the seeded language

loadGroup(state.taxon).then(()=>{buildMarkers();setView('explore');showLoading(false);
  try{const u=new URLSearchParams(location.search),at=u.get('at'),g=u.get('g');
    const go=()=>{if(at){const p=at.split(',').map(Number);if(p.length===2&&isFinite(p[0])&&isFinite(p[1])){map.setView([p[0],p[1]],8);exploreCell(p[0],p[1]);}}};
    if(g&&FILES[g]&&g!==state.taxon){state.taxon=g;taxonSel.value=g;loadGroup(g).then(()=>{buildMarkers();go();});}else{go();}
  }catch(e){}});
</script></body></html>"""

out = (HTML.replace("__FILES__", json.dumps(FILES, separators=(",", ":")))
           .replace("__OBJ__", json.dumps(OBJ))
           .replace("__PRESETS__", json.dumps(PRESETS))
           .replace("__DEFAULT__", json.dumps(DEFAULT)))
open("index.html", "w").write(out)
print("wrote index.html  ({:.0f} KB)".format(len(out) / 1024))
