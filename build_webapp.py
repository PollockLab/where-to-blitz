"""Builds index.html — an interactive trip planner for the Blitz the Gap
"where should I go to record biodiversity?" map. MapLibre GL basemap (OpenStreetMap +
style switcher); a weight slider per goal blended into a live "impact" score; a
start point + flexible time budget (minutes / hours / days); real driving routes
(OSRM) with drive time, field time, and travel CO2; and a low-carbon ranking
option. Answers "from here, with this much time, where do I maximise my impact?"."""
import json

# Canada-wide: fetch per-group at runtime (national grid fetched per-group at runtime is too big to inline).
# Inject only the group->filename map; the browser fetches each group's JSON on demand.
CA_INDEX = json.load(open("cluster_results/ca/index.json"))
FILES = CA_INDEX["files"]
# rows: [lat, lon, discover, conservation, env, staleness, urgency, travel_min, n_train]
OBJ = [
    {"key": "discover",     "name": "Discover the most species", "q": "go where few people have looked"},
    {"key": "conservation", "name": "Find rare species",          "q": "go where COSEWIC/SARA species at risk concentrate"},
    {"key": "env",          "name": "Cover every habitat",        "q": "go where the climate is under-sampled"},
    {"key": "staleness",    "name": "Freshest gaps",              "q": "go where lots was recorded long ago but little lately (iNaturalist recent vs all-time density)"},
    {"key": "urgency",      "name": "Sample before it's lost",    "q": "go where forest cover was recently lost (logging, fire, dieback)"},
]
# order matches OBJ: [discover, conservation, env, staleness, urgency]
# Issue #49: the Goal selector is reduced to four central goals (Katherine/Maho). Their intended
# inputs (Make a Splash, Missing Species in Canada, Too Hot to Handle, KBA-assessment lists) are
# not all wired yet; until the lab finalises the calculation, each goal is a TEMPORARY combination
# of the five real axes already computed [discover, conservation, env, staleness, urgency]:
#   Spatial Gap       = iNaturalist density + CHELSA climate gap
#   Species discovery = under-sampling + recent-vs-all-time density ("Revisit the Past")
#   Conservation      = COSEWIC/SARA at risk + recently changed habitat ("Too Hot to Handle")
# Getting Even is the separate categorical layer, added as the 'ge' option in the dropdown.
PRESETS = [
    {"name": "Spatial Gap",       "w": [1.0, 0, 0.5, 0, 0], "proj": "blitz-the-gap-2026-general",         "blurb": "Under-recorded places and under-sampled climates (iNaturalist density + CHELSA climate gap)."},
    {"name": "Species discovery", "w": [1.0, 0, 0, 0.6, 0], "proj": "blitz-the-gap-revisiting-the-past",  "blurb": "Where new-to-the-record species are likeliest: under-sampling plus cells recorded long ago but quiet lately."},
    {"name": "Conservation",      "w": [0, 1.0, 0, 0, 0.4], "proj": "blitz-the-gap-canada-s-most-wanted", "blurb": "Where species at risk concentrate, weighted toward recently changed habitat (COSEWIC/SARA via CAN-SAR + GBIF)."},
]
DEFAULT = PRESETS[0]["w"]

# Issue #17: the "Plan a trip" view (start point, travel budget, OSRM routing) is hidden for now —
# the team wants a simple gap-visualisation tool, not a trip planner. The code stays in place and
# dormant (flag flips it back) so a future "help plan a blitz" tool can reuse it.
PLAN_ENABLED = False
COMPARE_ENABLED = False   # Issue #71: the "Compare goals" view is stashed (kept dormant). Flag flips it back.

HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Where to Blitz the Gap</title>
<meta name="description" content="Find Canada's biodiversity gaps — head to an under-sampled spot, record what you see on iNaturalist, and fill the map. A companion planning tool for the Blitz the Gap bioblitz.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='88'%3E🍃%3C/text%3E%3C/svg%3E">
<meta name="theme-color" content="#0f1620">
<meta property="og:type" content="website">
<meta property="og:title" content="Where to Blitz the Gap">
<meta property="og:description" content="Find Canada's biodiversity gaps and plan a low-carbon trip to the best spot you can reach and get back from.">
<meta property="og:url" content="https://pollocklab.github.io/where-to-blitz/">
<meta property="og:image" content="https://pollocklab.github.io/where-to-blitz/og-image.png">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Where to Blitz the Gap">
<meta name="twitter:description" content="Find Canada's biodiversity gaps and plan a trip to the best spot you can reach.">
<meta name="twitter:image" content="https://pollocklab.github.io/where-to-blitz/og-image.png">
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"
  integrity="sha384-MinO0mNliZ3vwppuPOUnGa+iq619pfMhLVUXfC4LHwSCvF9H+6P/KO4Q7qBOYV5V" crossorigin="anonymous"/>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"
  integrity="sha384-SYKAG6cglRMN0RVvhNeBY0r3FYKNOJtznwA0v7B5Vp9tr31xAHsZC0DqkQ/pZDmj" crossorigin="anonymous"></script>
<script src="https://unpkg.com/pmtiles@3.2.1/dist/pmtiles.js"
  integrity="sha384-QfbOCebHNw8pQiPAOd2IFee2v2A5VYZxBk0+JGZ5H+3mfzVIp6zsQNkTsfGJot93" crossorigin="anonymous"></script>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0f1620;--panel:#172230;--ink:#e8eef5;--mut:#9fb2c6;--acc:rgb(139,168,132);--gd:#22c55e;--gold:#f0a000}
*{box-sizing:border-box}
html,body{margin:0;height:100%;font-family:'Inter',-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--ink)}
h1,.sec,.sechd .sec,.popsec,#viewtoggle button,#langbtn{font-family:'Space Grotesk','Inter',sans-serif}
:root{--bh:0px}
#app{display:flex;height:calc(100% - var(--bh));margin-top:var(--bh);position:relative}
/* Issue #20: the panel floats as a short card over a full-width map instead of a full-height column. */
/* The rounded card and the scroller are separate elements: a scrolling element with border-radius
   leaves a square-cornered paint seam at the top in Chromium, so #panel only rounds/clips (overflow:hidden,
   no scroll) and #panelInner does the scrolling inside it. */
#panel{position:absolute;z-index:1100;top:12px;left:12px;width:300px;max-height:calc(100% - 24px);display:flex;flex-direction:column;overflow:hidden;background:var(--panel);border-radius:12px;border:1px solid #2a3a4d;box-shadow:0 6px 22px rgba(0,0,0,.5)}
#panelInner{flex:1 1 auto;min-height:0;overflow-y:auto;padding:14px 15px 18px}
#map{flex:1;height:100%}
#panelToggle{position:fixed;top:calc(var(--bh) + 18px);left:300px;z-index:1300;width:26px;height:26px;border-radius:50%;background:var(--panel);color:var(--ink);border:1px solid #2a3a4d;cursor:pointer;font-size:15px;line-height:24px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.35);padding:0}
#panelToggle:hover{border-color:var(--acc);color:var(--acc)}
#panelToggle:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
body.panel-collapsed #panel{display:none}
body.panel-collapsed #panelToggle{left:8px;transform:none}
body.panel-collapsed #map{flex:1;height:100%}
body.panel-collapsed #loading{left:50%}
h1{font-size:20.5px;line-height:1.16;margin:0 0 2px}
.titlerow{display:flex;align-items:center;gap:10px;margin-bottom:2px}
.titlerow h1{flex:1 1 auto;margin:0}
.titlerow .logo{flex:0 0 auto;width:56px;height:56px;object-fit:contain}
.sub{color:var(--mut);font-size:14px;line-height:1.45;margin:0 0 8px}
.sec{font-size:12.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);margin:14px 0 8px;font-weight:700}
select{padding:7px 8px;background:#0e1722;color:var(--ink);border:1px solid #2a3a4d;border-radius:7px;font-size:16px}
select.full{width:100%}
.obj{margin:8px 0 8px}
.obj .top{display:flex;justify-content:space-between;align-items:baseline}
.obj .nm{font-weight:650;font-size:16px}
.obj .q{color:var(--mut);font-size:12.5px;margin:1px 0 5px}
.obj .v{color:var(--acc);font-weight:700;font-size:15px;font-variant-numeric:tabular-nums}
input[type=range]{width:100%;accent-color:var(--acc);margin:0}
.presets{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 2px}
.presets button{flex:1 1 auto;background:#10203044;color:var(--ink);border:1px solid #2a3a4d;border-radius:14px;padding:5px 10px;font-size:14px;cursor:pointer}
.presets button:hover{border-color:var(--acc);color:var(--acc)}
.presets button.on{border-color:var(--acc);background:var(--acc);color:#fff}
.startrow{display:flex;gap:6px;margin:4px 0}
.startrow button{flex:1;background:#10203044;color:var(--ink);border:1px solid #2a3a4d;border-radius:7px;padding:7px;font-size:14px;cursor:pointer}
.startrow button:hover{border-color:var(--gd)}
.startrow button.on{border-color:var(--gd);background:var(--gd);color:#04220f;font-weight:700}
/* Issue #69: the sidebar goes light to match openbiodiversity.ca. Remap the panel's theme vars (so the
   many inline color:var(--ink) emphasis spans stay visible on white) plus the few hard-coded dark control
   colours. Map chrome — legend, how-panel, popups — keeps the dark theme. */
#panel{--ink:#16202c;--mut:#5d6b7a;--panel:#fff;color:var(--ink);border-color:#dfe5ec;box-shadow:0 6px 22px rgba(20,40,70,.16)}
#panel select{background:#f4f7fa;border-color:#d4dde6}
#panel .infobox{background:#f4f7fa;border-color:#d4dde6}
#panel details.adv{border-top-color:#e2e8ef}
#panel .infobtn{border-color:#9aa7b5}
/* Issue #69: single language toggle, lifted out of the panel to sit left of the How-scored button. */
#topctrls{position:fixed;right:14px;top:calc(12px + var(--bh));z-index:1200;display:flex;align-items:stretch;gap:8px}
#langbtn{background:#fff;color:#1b2a3a;border:0;border-radius:9px;box-shadow:0 2px 9px rgba(0,0,0,.28);padding:7px 13px;font-size:12.5px;font-weight:700;letter-spacing:.04em;cursor:pointer}
#langbtn:hover{color:var(--acc)}
#langbtn:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.toggle{display:flex;align-items:center;gap:8px;margin:6px 0 2px;font-size:15px;cursor:pointer}
.toggle input{width:16px;height:16px;accent-color:var(--gd)}
#plan{width:100%;margin-top:10px;background:var(--gd);color:#04220f;border:0;border-radius:8px;padding:11px;font-size:17px;font-weight:700;cursor:pointer}
#plan:hover{filter:brightness(1.08)}
#trips{margin-top:10px;font-size:15px}
#trips .hd{color:var(--mut);font-size:12.5px;margin:6px 0 4px}
#trips .row{padding:6px 9px;border-radius:7px;background:#0e1722;margin:5px 0;cursor:pointer;border:1px solid #20303f}
#trips .row:hover,#trips .row.sel{border-color:var(--gold)}
#trips .row .t1{display:flex;justify-content:space-between;font-weight:650}
#trips .row .imp{color:var(--gold)}
#trips .row .t2{color:var(--mut);font-size:14px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#prospects{margin-top:8px}
#prospects .hd{color:var(--mut);font-size:12.5px;margin:6px 0 5px}
.prospects{display:flex;gap:7px;overflow-x:auto;padding-bottom:4px}
.prospects .sp{flex:0 0 86px;text-decoration:none;color:var(--ink)}
.prospects .sp img{width:86px;height:86px;object-fit:cover;border-radius:8px;display:block;border:1px solid #2a3a4d;background:#0e1722}
.prospects .sp .nm{font-size:12.5px;line-height:1.2;margin-top:3px}
.prospects .sp .ct{font-size:11px;color:var(--mut)}
.prospects .rare{background:var(--gold);color:#3a2a00;font-size:11px;font-weight:700;padding:0 4px;border-radius:6px;white-space:nowrap}
.prospects .first{background:var(--gd);color:#04220f;font-size:11px;font-weight:700;padding:0 4px;border-radius:6px;white-space:nowrap}
.gaptree{display:flex;flex-direction:column;gap:5px;margin-top:6px}
.gtrow{display:grid;grid-template-columns:80px 1fr auto;align-items:center;gap:9px;background:#0e1722;border:1px solid #2a3a4d;border-radius:7px;padding:6px 9px;cursor:pointer;text-align:left;color:var(--ink);font:inherit}
.gtrow:hover,.gtrow:focus-visible{border-color:var(--acc);outline:none}
.gtn{font-weight:700;font-size:12.5px}
.gtbar{height:8px;background:#1b2a3a;border-radius:5px;overflow:hidden}
.gtbar>span{display:block;height:100%}
.gtc{font-size:11px;color:var(--mut);white-space:nowrap}
.gtrow.gt-gap .gtbar>span{background:#d98a2b}
.gtrow.gt-part .gtbar>span{background:var(--gold)}
.gtrow.gt-ok .gtbar>span{background:var(--gd)}
#searchResults{position:absolute;left:0;right:0;top:100%;z-index:1000;background:#0e1722;border:1px solid #2a3a4d;border-radius:7px;margin-top:2px;overflow:hidden;display:none;box-shadow:0 4px 14px rgba(0,0,0,.4)}
#searchResults.open{display:block}
#searchResults .res{padding:8px 10px;cursor:pointer;font-size:12.5px;line-height:1.3;border-bottom:1px solid #1a2735}
#searchResults .res:last-child{border-bottom:0}
#searchResults .res:hover,#searchResults .res.on{background:#16263a}
#searchResults .res .sub{color:var(--mut);font-size:11px}
.legend{display:flex;align-items:center;gap:8px;margin-top:8px;font-size:12.5px;color:var(--mut)}
.bar{height:11px;flex:1;border-radius:6px;background:linear-gradient(90deg,#ffffd9,#edf8b1,#c7e9b4,#7fcdbb,#41b6c4,#1d91c0,#225ea8,#253494,#081d58)}
.foot{color:var(--mut);font-size:12.5px;line-height:1.5;margin-top:8px}
.sronly{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
#celltable{max-height:300px;overflow:auto;margin-top:6px}
#celltable table{width:100%;border-collapse:collapse;font-size:12.5px}
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
.maplibregl-popup-content{font-size:15px;padding:12px 16px 14px;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,.25)}.maplibregl-popup-content b{color:#0a2a44}.maplibregl-popup-close-button{font-size:18px;padding:0 5px;color:#667}
/* Issue #44: per-cell coverage + rare species live in the popup. Headings, a scroll-capped group list, and a 4-up species grid (no horizontal scroll). */
.popsec{font-size:12.5px;font-weight:700;color:#0a2a44;margin:9px 0 5px}
.popsec:first-child{margin-top:1px}
.popscroll{max-height:150px;overflow:hidden auto}
.popscroll .gaptree{margin-top:0}
/* #44 popup is ~258px wide: tighten the row so the coverage bar stays visible and the name never clips/side-scrolls (status word dropped from the count — see paintGapTree). */
.popscroll .gtrow{grid-template-columns:84px 1fr auto;gap:7px;padding:5px 8px}
.popscroll .gtn{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.popscroll .gtbar{min-width:22px}
.popgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
.popgrid a{text-decoration:none;color:#0a2a44;display:block}
.popgrid img{width:100%;aspect-ratio:1/1;object-fit:cover;border-radius:7px;border:1px solid #cdd;display:block;background:#eee}
.popgrid .nm{font-size:11px;line-height:1.15;margin-top:3px;max-height:28px;overflow:hidden}
.popgrid .ct{font-size:11px;color:#667;margin-top:1px}
.popcap{font-size:11px;line-height:1.3;color:#667;margin-top:6px}
#viewtoggle{position:fixed;top:calc(10px + var(--bh));left:50%;transform:translateX(-50%);z-index:1200;display:flex;background:#fff;border-radius:9px;box-shadow:0 2px 9px rgba(0,0,0,.28);overflow:hidden}
#viewtoggle button{border:0;background:#fff;color:#1b2a3a;padding:8px 15px;font-size:15px;font-weight:650;cursor:pointer}
#viewtoggle button.on{background:var(--acc);color:#fff}
#insights{position:fixed;left:0;top:0;right:0;bottom:0;z-index:1150;background:var(--bg);color:var(--ink);overflow-y:auto;padding:56px 22px 28px;display:none}
#insights .ihd{font-size:16px;line-height:1.55;max-width:980px;margin:0 auto 18px}
#insights .ihd b{color:var(--ink)}
#insights .ctrls{max-width:1200px;margin:0 auto 16px;display:flex;flex-wrap:wrap;gap:20px;font-size:14px}
#insights .ctrls .grp{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
#insights .ctrls .lbl{color:var(--mut);font-weight:700;text-transform:uppercase;font-size:12.5px;letter-spacing:.06em;margin-right:3px}/* match the Explore .sec label so the two views read as one product */
#insights .chip{background:#10203044;border:1px solid #2a3a4d;border-radius:14px;padding:5px 10px;cursor:pointer;user-select:none}
#insights .chip.on{background:var(--acc);border-color:var(--acc);color:#fff}
#insights .chip:focus-visible,.infobtn:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
#insights .matrix{display:grid;gap:10px;max-width:1200px;margin:0 auto;align-items:start}
#insights .gh{font-weight:700;font-size:14px;text-align:center;align-self:end;padding-bottom:3px}
#insights .gh .gq{color:var(--mut);font-size:11px;font-weight:400;line-height:1.2;display:block;margin-top:1px}
#insights .rl{font-weight:700;font-size:15px;display:flex;align-items:center}
#insights .cell{background:var(--panel);border:1px solid #2a3a4d;border-radius:9px;padding:6px;cursor:pointer;transition:border-color .12s}
#insights .cell:hover{border-color:var(--acc)}
#insights .cell canvas{width:100%;height:auto;background:#fbfbf6;border-radius:5px;display:block}
#insights .idis{max-width:980px;margin:18px auto 0;color:var(--mut);font-size:14px;line-height:1.6;border-top:1px solid #243446;padding-top:12px}
#insights .idis b{color:var(--ink)}
.sechd{display:flex;align-items:center;gap:6px;margin:14px 0 8px}
.sechd .sec{margin:0}
.infobtn{cursor:pointer;width:16px;height:16px;border-radius:50%;border:1px solid var(--mut);color:var(--mut);font:italic 700 11px/14px Georgia,serif;text-align:center;flex:0 0 auto;user-select:none}
.infobtn:hover{border-color:var(--acc);color:var(--acc)}
.infobox{display:none;background:#0e1722;border:1px solid #2a3a4d;border-radius:8px;padding:9px 11px;margin:0 0 8px;font-size:12.5px;line-height:1.5;color:var(--mut)}
.infobox.open{display:block}
.infobox b{color:var(--ink)}
.infobox ul{margin:5px 0 0;padding-left:15px}
.infobox li{margin:3px 0}
@media(max-width:640px){
  #app{flex-direction:column}
  #panel{position:static;width:100%;min-width:0;height:auto;max-height:50vh;border-radius:0;border:0;border-bottom:1px solid #0a1119;box-shadow:none}
  #map{height:50vh;flex:none}
  #viewtoggle{left:50%;top:auto;bottom:12px}
  #insights{left:0;top:0;height:100%;padding:54px 12px 24px}
  .infobtn{width:24px;height:24px;line-height:22px;font-size:13px}
}
#maplegend{position:fixed;bottom:24px;right:14px;z-index:1050;background:rgba(255,255,255,.93);border-radius:9px;padding:6px 10px;box-shadow:0 2px 10px rgba(0,0,0,.22);font-size:14px;color:#16233a;min-width:188px;max-width:244px;line-height:1.25}
#maplegend .lt{font-weight:700;font-size:14px}/* retained: reused by the Getting Even categorical legend title (the priority-legend title div was removed for #47) */
#maplegend .ramp{height:9px;border-radius:5px;background:linear-gradient(90deg,#ffffd9,#c7e9b4,#41b6c4,#225ea8,#081d58);margin:3px 0 2px}
#maplegend .ramp.magma{background:linear-gradient(90deg,#000004,#51127c,#b73779,#fc8961,#fcfdbf)}/* matches the iNaturalist density overlay colormap */
#maplegend .lab{display:flex;justify-content:space-between;font-size:12.5px;color:#46566a}
#maplegend .hint{margin-top:3px;color:#46566a;font-size:12.5px}
@media(max-width:640px){#maplegend{left:auto;right:8px;bottom:auto;top:calc(50vh + 8px);min-width:0;max-width:158px;padding:6px 8px}}
#loading{position:fixed;left:50%;top:46%;transform:translate(-50%,-50%);z-index:1200;background:rgba(255,255,255,.96);border-radius:10px;padding:11px 17px;box-shadow:0 3px 14px rgba(0,0,0,.25);font-size:14px;font-weight:600;color:#1b2a3a}
@media(max-width:640px){#loading{left:50%;top:74vh}}
#howbtn{display:flex;align-items:center;gap:6px;background:#fff;color:#1b2a3a;border:0;border-radius:9px;box-shadow:0 2px 9px rgba(0,0,0,.28);padding:7px 11px;font-size:12.5px;font-weight:650;cursor:pointer;max-width:260px}
#howbtn:hover{color:var(--acc)}
#howbtn:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
#howbtn .ic{font-size:14px}
#howpanel{position:fixed;right:14px;top:calc(52px + var(--bh));z-index:1200;width:330px;max-width:calc(100vw - 28px);max-height:calc(100vh - 72px - var(--bh));overflow-y:auto;background:var(--panel);color:var(--mut);border:1px solid #2a3a4d;border-radius:10px;box-shadow:0 6px 22px rgba(0,0,0,.45);padding:30px 15px 14px;display:none;font-size:12px;line-height:1.5}
#howpanel.open{display:block}
#howpanel .foot{margin-top:10px;font-size:11px;color:#7e91a6}
#howclose{position:absolute;top:6px;right:8px;background:transparent;border:0;color:var(--mut);font-size:20px;line-height:1;cursor:pointer;padding:2px 6px}
#howclose:hover{color:var(--ink)}
@media(max-width:640px){#topctrls{right:8px}#howbtn{max-width:none;font-size:12px}#howpanel{left:8px;right:8px;width:auto;top:calc(50vh + 44px);max-height:44vh}}
</style></head>
<body><div id="app">
<div id="panel" role="main"><div id="panelInner">
  <div class="titlerow">
    <h1 data-i18n-html="title_full">Where to<br><a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc);text-decoration:underline">Blitz the Gap</a></h1>
    <img class="logo" src="blitz-logo.png" alt="" width="56" height="56">
  </div>
  <div class="sechd"><span class="sec" data-i18n="sec_taxon">Species group</span></div>
  <select id="taxon" class="full" data-i18n-aria="aria_lifegroup" aria-label="Species group" style="margin-bottom:8px"></select>
  <div class="sechd"><span class="sec" data-i18n="sec_goal">Goal</span><span class="infobtn" data-i18n-title="info_btn" title="Where do these scores come from?" role="button" tabindex="0" data-i18n-aria="aria_about_data" aria-label="About the data" aria-expanded="false" onclick="const b=document.getElementById('taxinfo').classList.toggle('open');this.setAttribute('aria-expanded',b)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.click();}">i</span></div>
  <div class="infobox" id="taxinfo" data-i18n-html="taxinfo">
    <b>Where the scores come from.</b> <b style="color:var(--ink)">Higher priority = a spot where a new sighting adds more to what we know.</b> Canada-wide, on a 0.25° (~25&nbsp;km) grid.
    <ul>
      <li><b>Nationally the real signals are <span style="color:var(--ink)">under-sampling</span> (“Discover the most species”), <span style="color:var(--ink)">climate-coverage</span> (“Cover every habitat”) and <span style="color:var(--ink)">recent forest loss</span> (“Sample before it’s lost”).</b> Computed from real data (<a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a> density + CHELSA climate + Hansen forest loss); travel time is real too.</li>
      <li><b>Find species at risk</b> is now real: COSEWIC/SARA assessments (<a href="https://osf.io/e4a58/" target="_blank" rel="noopener" style="color:var(--acc)">CAN-SAR</a>) mapped to iNaturalist/GBIF occurrences — at-risk-species richness per cell (assessed species only). <b>Freshest gaps</b> is now real too: iNaturalist recent (last 5 yr) vs all-time density — cells well-recorded historically but quiet lately.</li>
      <li>The original <b>B.C. pilot had all five axes from real iNat history</b>; the national rollout has four of five real and is filling in the last (rare species).</li>
      <li><b>“All biodiversity”</b> is iNaturalist’s total observation density across all taxa; pick a single group to focus — 10 are available, from plants & insects to fishes, fungi & molluscs.</li>
    </ul>
    A planning aid, not ground truth — please obscure sensitive species and respect Indigenous data sovereignty. <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">How Blitz the Gap works →</a>
  </div>
  <select id="criteria" class="full" data-i18n-aria="aria_criteria" aria-label="Criteria" style="margin-bottom:8px"></select>

  <div id="tripui">
  <div class="sec" data-i18n="your_trip">Your trip</div>
  <div style="position:relative">
    <input id="placeSearch" type="text" placeholder="Search a town or address" data-i18n-ph="search_ph" autocomplete="off" data-i18n-aria="aria_search" aria-label="Search for a start place" role="combobox" aria-haspopup="listbox" aria-expanded="false" aria-controls="searchResults" aria-autocomplete="list"
      style="width:100%;padding:8px 9px;background:#0e1722;color:var(--ink);border:1px solid #2a3a4d;border-radius:7px;font-size:14px">
    <div id="searchResults" role="listbox" data-i18n-aria="aria_search_results" aria-label="Search results"></div>
  </div>
  <div class="startrow" style="margin-top:6px">
    <button id="setMe" data-i18n="my_location">Locate me</button>
  </div>
  <div style="color:var(--mut);font-size:11px;margin:4px 0 7px" data-i18n-html="start_hint">Start: <b id="startlbl">Vancouver</b> · <b style="color:var(--gd)">tap the map</b> to move it.</div>
  <div class="startrow" id="modes"></div>
  <div id="modewhy" style="color:var(--mut);font-size:11px;margin:2px 0 0"></div>
  <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
    <span style="font-size:13.5px" data-i18n="time">Time</span>
    <select id="unit" data-i18n-aria="aria_time_unit" aria-label="Time unit"><option value="Minutes" data-i18n="unit_minutes">Minutes</option><option value="Hours" selected data-i18n="unit_hours">Hours</option><option value="Days" data-i18n="unit_days">Days</option></select>
    <span class="v" id="budv" style="margin-left:auto;color:var(--gold);font-weight:700">5h</span>
  </div>
  <input type="range" id="budget" min="1" max="14" step="0.5" value="5" data-i18n-aria="aria_time_budget" aria-label="Time budget" style="margin-top:6px">
  <details class="adv"><summary data-i18n="more_options">More options</summary>
    <div style="display:flex;gap:8px;align-items:center;margin:4px 0 8px">
      <span style="font-size:13.5px" data-i18n="max_travel">Max travel each way</span>
      <select id="maxleg" data-i18n-aria="aria_max_travel" aria-label="Max travel each way" style="margin-left:auto">
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
      <select id="minratio" data-i18n-aria="aria_worth_drive" aria-label="Worth the drive" style="margin-left:auto">
        <option value="0" data-i18n="ratio_any">Any trip</option>
        <option value="0.5" selected data-i18n="ratio_half">Record ≥ ½ the round trip</option>
        <option value="1" data-i18n="ratio_one">Record ≥ the round trip</option>
        <option value="2" data-i18n="ratio_two">Record ≥ 2× the round trip</option>
      </select>
    </div>
    <div style="color:var(--mut);font-size:11px;margin:0 0 8px" data-i18n-html="worth_hint">Drops mostly-driving trips. <b style="color:var(--ink)">Round trip = there and back</b> (both legs counted). Default: field time ≥ half your round-trip drive; pick "Any" for long hauls.</div>
    <label class="toggle"><input type="checkbox" id="lowc"> <span data-i18n="lowc">Prefer low-carbon trips</span></label>
    <div style="color:var(--mut);font-size:11px;margin:0 0 5px" data-i18n="lowc_hint">Rank by impact per kg of travel CO₂.</div>
    <label class="toggle"><input type="checkbox" id="startProsp"> <span data-i18n="startprosp">Also show species around my start</span></label>
  </details>
  <button id="plan" data-i18n="plan_trip">Plan my trip →</button>
  <div id="trips"></div>
  <!-- Issue #45: per-cell coverage + species now live in the cell popup (#44); only the plan-mode "species around my start" list remains in the sidebar, so #prospects sits inside #tripui and #gaptree is gone. -->
  <div id="prospects" data-idle="1"></div>
  </div>

  <div class="sechd"><span class="sec" data-i18n="map_style">Map style</span></div>
  <select id="basemap" class="full" data-i18n-aria="aria_map_style" aria-label="Map style" style="margin-bottom:8px"></select>
  <div id="layertoggles" style="display:none;margin-top:8px">
    <label class="toggle" style="margin:4px 0"><input type="checkbox" id="tgRoads" checked> <span data-i18n="roads">Roads</span></label>
    <label class="toggle" style="margin:4px 0"><input type="checkbox" id="tgLabels" checked> <span data-i18n="labels_places">Labels &amp; places</span></label>
    <div style="color:var(--mut);font-size:11px;line-height:1.4" data-i18n="vector_hint">Vector basemap — toggle layers like Maputnik. Other styles are raster (roads baked in).</div>
  </div>
  <label class="toggle" style="display:none"><input type="checkbox" id="tgCoverage"> <span data-i18n="inat_coverage">iNaturalist coverage</span></label><!-- driven by the "iNaturalist sampling density" map style (#20) -->
  <label class="toggle" style="display:none"><input type="checkbox" id="tgGettingEven"> <span data-i18n="getting_even">Getting Even — which group to record</span></label><!-- driven by the "Getting Even" criterion (#20) -->
  <div style="display:none" data-i18n="ge_hint">Each cell is coloured by the most under-represented taxonomic group there (birds excluded — already well covered by eBird) — our finer-resolution take on the official "Getting Even" challenge. From iNaturalist observation density: a sample, not a census.</div>
  <div class="infobox" id="geinfo" data-i18n-html="ge_method"></div>
  <div id="opacityRow" style="display:none"><!-- #46: only shown while a data overlay (density style) is active; basemap brightness is otherwise locked at 100% -->
  <div style="display:flex;justify-content:space-between;margin:9px 0 0"><span id="bople" style="font-size:12.5px" data-i18n="data_opacity">Density opacity</span><span class="v" id="bopv" style="color:var(--acc)">80%</span></div>
  <input type="range" id="baseop" min="0.1" max="1" step="0.05" value="1" data-i18n-aria="aria_data_opacity" aria-label="Density opacity">
  </div>

  <div id="celltable" class="sronly" role="region" data-i18n-aria="aria_top_cells" aria-label="Top cells (accessible list)"></div>
</div></div>
<button id="panelToggle" type="button" data-i18n-aria="hide_panel" data-i18n-title="hide_panel" aria-label="Hide the panel" title="Hide the panel">‹</button>
<div id="map" role="application" data-i18n-aria="aria_map" aria-label="Interactive priority map (screen-reader users: use the Top cells list)"></div>
<div id="viewtoggle" role="navigation" data-i18n-aria="aria_map_view" aria-label="Map view"><button id="vexplore" class="on" aria-pressed="true" data-i18n="view_explore">Explore</button><button id="vplan" aria-pressed="false" data-i18n="view_plan">Plan a trip</button><button id="vcompare" aria-pressed="false" data-i18n="view_compare">Compare goals</button></div>
<div id="loading" role="status" aria-live="polite" data-i18n="loading">Loading the map…</div>
<div id="maplegend" role="region" data-i18n-aria="aria_map_legend" aria-label="Map legend"><div class="ramp"></div><div class="lab"><span data-i18n="legend_low">well-sampled</span><span data-i18n="legend_high">biggest gaps</span></div><div class="hint" id="legendrel" style="display:none;color:#7a5b00" data-i18n="legend_rel">Colours rescaled to this view — not comparable across zoom levels.</div></div>
<div id="topctrls"><button id="langbtn" type="button" data-i18n-aria="aria_language" data-i18n-title="lang_switch" aria-label="Language" title="Switch language">EN</button><button id="howbtn" type="button" aria-expanded="false" aria-controls="howpanel" data-i18n-title="how_scored" title="How impact is scored & data sources"><span data-i18n="how_scored">How impact is scored & data sources</span></button></div>
<div id="howpanel" role="region" data-i18n-aria="how_scored" aria-label="How impact is scored & data sources">
  <button id="howclose" type="button" data-i18n-aria="aria_close" data-i18n-title="aria_close" aria-label="Close" title="Close">×</button>
  <div class="legend" style="margin-top:2px"><span data-i18n="skip">skip</span><div class="bar"></div><span data-i18n="go_here">go here</span></div>
  <div style="color:var(--mut);font-size:11px;line-height:1.45;margin-top:7px" data-i18n-html="impact_expl">Each goal is scored <b style="color:var(--ink)">0–1 per cell</b>; your slider weights combine them, then cells are <b style="color:var(--ink)">ranked against each other</b> and shown as <b style="color:var(--ink)">impact 0–100</b> (a percentile — 100 = top-priority cell shown). Hover a cell to see which goals drive it.</div>
  <div style="color:var(--mut);font-size:11px;line-height:1.5;margin-top:9px" data-i18n-html="axis_method"></div>
  <div style="margin-top:9px"><a href="https://pollocklab.github.io/where-to-blitz/where-to-blitz-walkthrough.html" target="_blank" rel="noopener" style="color:var(--acc);font-size:12px;font-weight:600" data-i18n="methodology_link">Full methodology &amp; data — trace every number →</a></div>
  <p class="foot" data-i18n-html="foot"><b style="color:var(--ink)">How it works:</b> <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">Blitz the Gap</a> is a Canada-wide bioblitz — head to a high-priority spot, record what you see on <a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a>, and your research-grade sightings flow into the <a href="https://www.inaturalist.org/projects/blitz-the-gap-2026-general" target="_blank" rel="noopener" style="color:var(--acc)">2026 project</a>, filling the map's gaps. Blitz the Gap is led by the <a href="https://pollocklab.github.io/blitz-the-gap/" target="_blank" rel="noopener" style="color:var(--acc)">Pollock Lab</a> at McGill University; this is a work-in-progress companion tool, not an official project page.<br><br>Nationally, the robust priority signal is <b style="color:var(--ink)">under-sampling</b> (iNaturalist density) + <b style="color:var(--ink)">climate coverage</b> (CHELSA); rarity and freshness are now real (rarity = COSEWIC/SARA species at risk via CAN-SAR + GBIF; freshness = iNaturalist recent vs all-time density). Drive/cycle/walk routes from OSRM (FOSSGIS); travel time from Weiss 2018. Driving CO₂ ≈ 0.18 kg/km; cycling/walking zero. A planning aid — obscure sensitive-species locations and respect Indigenous data-sovereignty before any public release.<br><br>This map spans many <b style="color:var(--ink)">Indigenous territories</b> — see whose at <a href="https://native-land.ca" target="_blank" rel="noopener" style="color:var(--acc)">native-land.ca</a>, and seek consent before recording on their lands.</p>
</div>
<div id="insights"></div>
</div>

<script>
const FILES=__FILES__, OBJ=__OBJ__, PRESETS=__PRESETS__, DEFAULT=__DEFAULT__;
const PLAN_ENABLED=__PLAN_ENABLED__||/[?&]plan=1/.test(location.search);   // issue #17: hidden by default; ?plan=1 is a no-clutter escape hatch for the team (no rebuild)
const COMPARE_ENABLED=__COMPARE_ENABLED__||/[?&]compare=1/.test(location.search);   // issue #71: stashed; ?compare=1 re-enables

// ---- i18n (EN / Canadian French) ----------------------------------------
// Static UI chrome is keyed by string id and applied to [data-i18n*] nodes.
// Dynamic strings (trips, prospects, popups) call t('id', {vars}) at build time.
// OBJ/PRESET/group names below are indexed to keep the data structures stable.
const I18N={
  en:{
    title_full:`Where to<br><a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc);text-decoration:underline">Blitz the Gap</a>`,
    sub:"Choose what counts as <b>impact</b>, then <b>explore</b> the priority map — or <b>plan a trip</b> to the best spot you can reach and get back from.",
    sec_group:"Life group & goal", sec_taxon:"Species group", sec_goal:"Goal",
    info_btn:"Where do these scores come from?",
    taxinfo:`<b>Where the scores come from.</b> <b style="color:var(--ink)">Higher priority = a spot where a new sighting adds more to what we know.</b> Canada-wide, on a 0.25° (~25&nbsp;km) grid.
      <ul>
      <li><b>Nationally the real signals are <span style="color:var(--ink)">under-sampling</span> (“Discover the most species”), <span style="color:var(--ink)">climate-coverage</span> (“Cover every habitat”) and <span style="color:var(--ink)">recent forest loss</span> (“Sample before it’s lost”).</b> Computed from real data (<a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a> density + CHELSA climate + Hansen forest loss); travel time is real too.</li>
      <li><b>Find species at risk</b> is now real: COSEWIC/SARA assessments (<a href="https://osf.io/e4a58/" target="_blank" rel="noopener" style="color:var(--acc)">CAN-SAR</a>) mapped to iNaturalist/GBIF occurrences — at-risk-species richness per cell (assessed species only). <b>Freshest gaps</b> is now real too: iNaturalist recent (last 5 yr) vs all-time density — cells well-recorded historically but quiet lately.</li>
      <li>The original <b>B.C. pilot had all five axes from real iNat history</b>; the national rollout has four of five real and is filling in the last (rare species).</li>
      <li><b>“All biodiversity”</b> is iNaturalist’s total observation density across all taxa; pick a single group to focus — 10 are available, from plants & insects to fishes, fungi & molluscs.</li>
      </ul>
      A planning aid, not ground truth — please obscure sensitive species and respect Indigenous data sovereignty. <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">How Blitz the Gap works →</a>`,
    finetune:"Fine-tune the five goals",
    your_trip:"Your trip",
    search_ph:"Search a town or address",
    my_location:"Locate me",
    start_alt:"Trip start — drag to move", start_tip:"Start — drag me, or tap the map",
    aria_language:"Language", lang_switch:"Switch language", aria_about_data:"About the data", aria_lifegroup:"Species group", aria_criteria:"Criteria",
    aria_search:"Search for a start place", aria_search_results:"Search results", aria_time_unit:"Time unit",
    aria_time_budget:"Time budget", aria_max_travel:"Max travel each way", aria_worth_drive:"Worth the drive",
    aria_map_style:"Map style", aria_map_brightness:"Map brightness", aria_data_opacity:"Density opacity", aria_top_cells:"Top cells (accessible list)",
    aria_map:"Interactive priority map (screen-reader users: use the Top cells list)", aria_map_view:"Map view",
    aria_map_legend:"Map legend", aria_close:"Close",
    rank_aria:(i,la,lo,sc)=>`Rank ${i}: latitude ${la}, longitude ${lo}, score ${sc} of 100`,
    prio_map_aria:(grp,goal)=>`${grp}, ${goal} priority map`,
    vancouver:"Vancouver",
    start_hint:`Start: <b id="startlbl">—</b> · <b style="color:var(--gd)">tap the map</b>, search, or locate me to set it.`,
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
    lowc:"Prefer low-carbon trips",
    lowc_hint:"Rank by impact per kg of travel CO₂.",
    startprosp:"Also show species around my start",
    plan_trip:"Plan my trip →",
    prospects_idle:"Tap a cell to see what to record there.",
    gaptree_lookup:"Reading taxonomic coverage…",
    gaptree_sparse:"Too few nearby records to rank groups here yet — every sighting helps fill the map.",
    gaptree_err:"Couldn’t read coverage just now — tap the cell again.",
    pop_groups_hd:"Under-sampled groups", pop_rare_hd:"Top species to record here",
    gt_caveat:"iNaturalist records — what's been logged nearby, a proxy for effort, not a count of what's there.",
    gt_gap:"gap", gt_partial:"partial", gt_ok:"well recorded",
    gt_count:(c,n)=>`${c} here · ~${n} nearby`,   // two observed iNat record tallies, not a ratio-of-total: "X of ~Y" wrongly read as complete coverage at equality (5 of ~5). ~Y is a floor, never true richness.
    gt_switch:(g)=>`Switch the map to ${g}`,
    map_style:"Map style",
    style_standard:"Standard", style_satellite:"Satellite", style_terrain:"Terrain", style_inat_density:"iNaturalist sampling density",
    crit_ge:"Getting Even (under-represented groups)",
    roads:"Roads", labels_places:"Labels & places",
    vector_hint:"Vector basemap — toggle layers like Maputnik. Other styles are raster (roads baked in).",
    inat_coverage:"iNaturalist coverage",
    coverage_hint:`Where data already is (bright = well-sampled, dark = gaps) — an iNaturalist-density "light up the map" layer (Biodiversité Québec), for the current group.`,
    getting_even:"Getting Even (under-represented groups)",
    ge_hint:`Each cell is coloured by the most under-represented taxonomic group there (birds excluded — already well covered by eBird) — our finer-resolution take on the official "Getting Even" challenge. From iNaturalist observation density: a sample, not a census.`,
    ge_info_btn:"How is the recommended group worked out?",
    ge_method:`<b>How "which group to record" is worked out.</b> Tap any cell and the app asks iNaturalist, live, for each group of life (birds excluded — eBird already covers them):
      <ul>
      <li>how many distinct research-grade species are recorded <b style="color:var(--ink)">in this cell</b>, versus</li>
      <li>how many exist in the <b style="color:var(--ink)">~50&nbsp;km around it</b>.</li>
      </ul>
      A group's coverage is the first divided by the second, then divided by the cell's overall recording rate — so an average group sits at <b style="color:var(--ink)">1</b>. Groups well below 1 are under-recorded relative to their neighbours and rise to the top, <b style="color:var(--ink)">biggest relative gap first</b>. That normalising step surfaces the real gaps even where everything is under-recorded (one cell can't hold a whole region's diversity). The map colours each cell by its single worst group; tap for the full ranked tree, tap a group to map it. iNaturalist density — a sample, not a census.`,
    ge_cats:["Fishes","Fungi","Reptiles & Amphibians","Invertebrates","Mammals","Plants"],
    ge_all:"All groups under-sampled",
    canada_only:"Canada only",
    canada_only_hint:"Hides cells across the US border, where the bright band is a data edge (the Canadian layer stops at the border), not a real gap. Approximate boundary.",
    more_presets:"More ▾",
    fewer_presets:"Fewer ▴",
    methodology_link:"Full methodology & data — trace every number →",
    hide_panel:"Hide the panel (more map)",
    show_panel:"Show the panel",
    zoom_scale:"Rescale colours to the current view",
    zoom_scale_hint:`Ranks cells against what's on screen, so local gaps stand out when you zoom in. Off = ranked across all of Canada (a dark cell means the same everywhere).`,
    legend_rel:"Colours rescaled to this view — not comparable across zoom levels.",
    map_brightness:"Map brightness",
    data_opacity:"Density opacity",
    how_scored:"How impact is scored & data sources",
    skip:"skip", go_here:"go here",
    impact_expl:`Each goal is scored <b style="color:var(--ink)">0–1 per cell</b>; your slider weights combine them, then cells are <b style="color:var(--ink)">ranked against each other</b> and shown as <b style="color:var(--ink)">impact 0–100</b> (a percentile — 100 = top-priority cell shown). Hover a cell to see which goals drive it.`,
    axis_method:`<b style="color:var(--ink)">How each goal is scored (0–1 per cell):</b><br>• <b style="color:var(--ink)">Discover the most species</b> — where few have recorded: inverse iNaturalist observation density (Biodiversité Québec).<br>• <b style="color:var(--ink)">Find species at risk</b> — at-risk species recorded nearby, weighted by status (Endangered 3 / Threatened 2 / Special Concern 1): COSEWIC/SARA assessments (CAN-SAR) × GBIF occurrences.<br>• <b style="color:var(--ink)">Cover every habitat</b> — climate types rarely recorded: CHELSA climate “surprisal”.<br>• <b style="color:var(--ink)">Freshest gaps</b> — much recorded in the past, little lately: iNaturalist recent vs all-time density.<br>• <b style="color:var(--ink)">Sample before it's lost</b> — recent habitat change: Hansen Global Forest Change forest loss.`,
    foot:`<b style="color:var(--ink)">How it works:</b> <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">Blitz the Gap</a> is a Canada-wide bioblitz — head to a high-priority spot, record what you see on <a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a>, and your research-grade sightings flow into the <a href="https://www.inaturalist.org/projects/blitz-the-gap-2026-general" target="_blank" rel="noopener" style="color:var(--acc)">2026 project</a>, filling the map's gaps. Blitz the Gap is led by the <a href="https://pollocklab.github.io/blitz-the-gap/" target="_blank" rel="noopener" style="color:var(--acc)">Pollock Lab</a> at McGill University; this is a work-in-progress companion tool, not an official project page.<br><br>Nationally, the robust priority signal is <b style="color:var(--ink)">under-sampling</b> (iNaturalist density) + <b style="color:var(--ink)">climate coverage</b> (CHELSA); rarity and freshness are now real (rarity = COSEWIC/SARA species at risk via CAN-SAR + GBIF; freshness = iNaturalist recent vs all-time density). Drive/cycle/walk routes from OSRM (FOSSGIS); travel time from Weiss 2018. Driving CO₂ ≈ 0.18 kg/km; cycling/walking zero. A planning aid — obscure sensitive-species locations and respect Indigenous data-sovereignty before any public release.<br><br>This map spans many <b style="color:var(--ink)">Indigenous territories</b> — see whose at <a href="https://native-land.ca" target="_blank" rel="noopener" style="color:var(--acc)">native-land.ca</a>, and seek consent before recording on their lands.`,
    top_cells:"Top cells (accessible list)",
    view_explore:"Explore", view_plan:"Plan a trip", view_compare:"Compare goals",
    loading:"Loading the map…",
    load_error:"⚠ Couldn't load the map — check your connection.",
    retry:"↻ Retry",
    legend_title:"Where to blitz", legend_low:"well-sampled", legend_high:"biggest gaps",
    density_low:"few records", density_high:"many records", density_legend_hint:"iNaturalist records (sharpens to ~300 m)",
    axis_legend:[["well-sampled","biggest gaps"],["no species at risk","many at risk"],["climate well-covered","climate under-sampled"],["recently recorded","long overdue"],["stable","recently changed"]],
    legend_hint:`Darker = higher priority. <span id="legendtap">Tap a cell to see what to record.</span>`,
    // dynamic
    legendtap_explore:"Tap a cell to see what to record.",
    legendtap_plan_start:"Tap the map to set your start.",
    legendtap_plan_dest:"Tap the map to choose your destination.",
    near:"near", here:"here", my_loc_short:"my location", my_area_ip:"my area (from IP)",
    locating:"locating…", loc_unavail:"location unavailable — try the search above",
    cells_loading:t=>`Loading ${t}…`,
    prospects_where_start:"Around your start", pop_go_title2:"Your destination",
    prospects_lookup:"Looking up what lives here…",
    prospects_none:"No research-grade records here yet — you could be the first to document what lives here.",
    prospects_err:"Couldn’t load species just now — tap the cell again.",
    prospects_hd:(where,n,nearby)=>`<b style="color:var(--ink)">${where}</b> · ${n} species recorded on iNaturalist. Worth looking for${nearby?' (nearby)':''}:`,
    rare:"rare nearby", gap:"gap",
    here_count:n=>`${n} here`, near_count:n=>`${n} within ~40 km`, new_here:"new here", not_here_tag:"not here yet",
    inat_caveat:"Counts are iNaturalist observations — what people have logged, not a complete species census.",
    explore_all:"Explore all on iNaturalist →", log_sighting:"Log a sighting", for_challenge:"for this challenge",
    join:"join →",
    more_challenges:"+ see all official challenges →",
    finding_routes:m=>`Finding real ${m} routes…`,
    mode_why:(label,green)=>green?`${label} — the greenest way to reach a gap in your time budget.`:`${label} — the nearest worthwhile gap is too far to walk or cycle in time.`,
    best_trips:(bud,lowc,est)=>`Best trips within ${bud} — most <b style="color:var(--ink)">impact × time you'd get to record</b>${lowc?', per kg CO₂':''}${est?' (some times estimated)':''}:`,
    or_skip:"Or skip the drive:",
    no_fit:(bud,mode)=>`No round trip fits ${bud} by ${mode} from here. The best move is to record where you are — or go farther with more time:`,
    farther:(bud,car)=>`Farther afield — over ${bud} round trip, but yours with more time${car?' or by 🚗':''}:`,
    right_here:"Right where you are",
    each_way:"each way", field:"field", in_field:"in the field", round:"round", over:"over",
    no_extra:"no extra travel — record the cell you're already in",
    pop_here_title:"Record right where you are",
    pop_here_sub:"you're in this ~25 km cell — no extra travel needed",
    pop_here_foot:"spend your time recording, not driving here",
    pop_go_title:"Go to this area", pop_go_sub:"— anywhere in the highlighted ~25 km cell",
    pop_centre:"centre", pop_impact:"impact",
    pop_round:"round trip", pop_estimated:"(estimated)",
    pop_over_budget:bud=>`round trip — over your ${bud}`,
    car_free:"car-free",
    table_caption:"Top 40 cells for your goal mix & group, highest first. Tap a row to open it.",
    table_rank:"#", table_latlon:"Lat, lon", table_score:"Score",
    ins_hd:`<b>The same place — different goals, different life groups.</b> Each map shades every Canadian cell by one goal (<b>darker = go there</b>). The hot zones shift between goals (a value choice) and between groups (different species fill different gaps). Pick the rows & columns; tap any map to open it in the planner.`,
    ins_groups:"Groups (rows)", ins_goals:"Goals (columns)",
    ins_onhold:`<b>Comparison on hold</b> — one of the two selected goals has no variation across the groups shown, so a correlation isn't meaningful here. Pick two goals that both vary.`,
    ins_spearman:(g1,g2,dis,verdict)=>`<b>${g1} vs ${g2}</b> — Spearman ρ between cell rankings (across all cells; negative = opposite places): ${dis}. ${verdict}`,
    ins_v_all_diff:"The two goals point to <b>different places in every group shown</b> — the most under-sampled cells are not where the species at risk are.",
    ins_v_agree:"For the groups shown, the two goals mostly <b>agree</b> here.",
    ins_v_some:(neg,n)=>`They point to different places in <b>${neg} of ${n}</b> groups shown — under-sampling and at-risk species often diverge, but not always.`,
    // OBJ / PRESET / group display names (indexed)
    obj_name:["Discover the most species","Find species at risk","Cover every habitat","Freshest gaps","Sample before it's lost"],
    obj_q:["go where few people have looked","go where COSEWIC/SARA species at risk concentrate","go where the climate is under-sampled","go where lots was recorded long ago but little lately (iNaturalist recent vs all-time density)","go where forest cover was recently lost (logging, fire, dieback)"],
    preset_name:["Spatial Gap","Species discovery","Conservation"],
    preset_blurb:["Under-recorded places and under-sampled climates (iNaturalist density + CHELSA climate gap).","Where new-to-the-record species are likeliest: under-sampling plus cells recorded long ago but quiet lately.","Where species at risk concentrate, weighted toward recently changed habitat (COSEWIC/SARA via CAN-SAR + GBIF)."],
    group:{Amphibia:"Amphibians",Aves:"Birds",Insecta:"Insects",Mammalia:"Mammals",Reptilia:"Reptiles",Plantae:"Plants",Fungi:"Fungi",Actinopterygii:"Fishes",Arachnida:"Arachnids",Mollusca:"Molluscs","All biodiversity":"All biodiversity"},
    modes:{Walk:"Walk",Cycle:"Cycle",Drive:"Drive"},
  },
  fr:{
    title_full:`Où aller pour<br><a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc);text-decoration:underline">Blitz the Gap</a>`,
    sub:"Choisissez ce qui compte comme <b>impact</b>, puis <b>explorez</b> la carte des priorités — ou <b>planifiez une sortie</b> vers le meilleur endroit que vous pouvez atteindre et d'où vous pouvez revenir.",
    sec_group:"Groupe d'espèces et objectif", sec_taxon:"Groupe d'espèces", sec_goal:"Objectif",
    info_btn:"D'où viennent ces scores?",
    taxinfo:`<b>D'où viennent les scores.</b> <b style="color:var(--ink)">Priorité élevée = un endroit où une nouvelle observation ajoute le plus à nos connaissances.</b> À l'échelle du Canada, sur une grille de 0,25° (~25&nbsp;km).
      <ul>
      <li><b>À l'échelle nationale, les vrais signaux sont la <span style="color:var(--ink)">sous-représentation</span> (« Découvrir le plus d'espèces »), la <span style="color:var(--ink)">couverture climatique</span> (« Couvrir chaque habitat ») et la <span style="color:var(--ink)">perte forestière récente</span> (« Échantillonner avant qu'il soit trop tard »).</b> Calculés à partir de données réelles (densité <a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a> + climat CHELSA + perte forestière Hansen); le temps de déplacement est réel aussi.</li>
      <li><b>Trouver des espèces en péril</b> est maintenant réel : évaluations COSEWIC/SARA (CAN-SAR) liées aux observations iNaturalist/GBIF — richesse en espèces en péril par cellule (espèces évaluées). <b>Lacunes les plus fraîches</b> est aussi réel : densité iNaturalist récente (5 ans) vs historique — cellules bien observées autrefois mais calmes récemment.</li>
      <li>Le <b>projet pilote en C.-B. comportait les cinq axes tirés de l'historique iNaturalist</b>; le déploiement national en compte quatre sur cinq et complète le dernier (espèces rares).</li>
      <li><b>« Toute la biodiversité »</b> correspond à la densité totale d’observations iNaturalist (tous les taxons); choisissez un seul groupe pour cibler — 10 sont disponibles, des plantes et insectes aux poissons, champignons et mollusques.</li>
      </ul>
      Un outil de planification, pas une vérité absolue — veuillez masquer les espèces sensibles et respecter la souveraineté des données autochtones. <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">Fonctionnement de Blitz the Gap →</a>`,
    finetune:"Ajuster les cinq objectifs",
    your_trip:"Votre sortie",
    search_ph:"Rechercher une ville ou une adresse",
    my_location:"Me localiser",
    start_alt:"Départ de la sortie — glissez pour déplacer", start_tip:"Départ — glissez-moi, ou touchez la carte",
    aria_language:"Langue", lang_switch:"Changer de langue", aria_about_data:"À propos des données", aria_lifegroup:"Groupe d'espèces", aria_criteria:"Critère",
    aria_search:"Rechercher un lieu de départ", aria_search_results:"Résultats de recherche", aria_time_unit:"Unité de temps",
    aria_time_budget:"Temps disponible", aria_max_travel:"Trajet max (aller)", aria_worth_drive:"Vaut le déplacement",
    aria_map_style:"Style de carte", aria_map_brightness:"Luminosité de la carte", aria_data_opacity:"Opacité des données", aria_top_cells:"Meilleures cellules (liste accessible)",
    aria_map:"Carte de priorité interactive (lecteurs d'écran : utilisez la liste des meilleures cellules)", aria_map_view:"Vue de la carte",
    aria_map_legend:"Légende de la carte", aria_close:"Fermer",
    rank_aria:(i,la,lo,sc)=>`Rang ${i} : latitude ${la}, longitude ${lo}, score ${sc} sur 100`,
    prio_map_aria:(grp,goal)=>`${grp}, ${goal} carte de priorité`,
    vancouver:"Vancouver",
    start_hint:`Départ : <b id="startlbl">—</b> · <b style="color:var(--gd)">touchez la carte</b>, cherchez, ou localisez-moi pour le définir.`,
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
    lowc:"Préférer les sorties à faible carbone",
    lowc_hint:"Classer par impact par kg de CO₂ de déplacement.",
    startprosp:"Montrer aussi les espèces près de mon départ",
    plan_trip:"Planifier ma sortie →",
    prospects_idle:"Touchez une cellule pour voir quoi observer.",
    gaptree_lookup:"Lecture de la couverture taxonomique…",
    gaptree_sparse:"Trop peu d’observations à proximité pour classer les groupes ici — chaque observation aide à combler la carte.",
    gaptree_err:"Lecture de la couverture impossible pour l’instant — touchez la cellule à nouveau.",
    pop_groups_hd:"Groupes sous-échantillonnés", pop_rare_hd:"Espèces à noter en priorité ici",
    gt_caveat:"Observations iNaturalist — ce qui est consigné à proximité, un indice d'effort, pas un inventaire de ce qui s'y trouve.",
    gt_gap:"lacune", gt_partial:"partielle", gt_ok:"bien documenté",
    gt_count:(c,n)=>`${c} ici · ~${n} à proximité`,
    gt_switch:(g)=>`Afficher ${g} sur la carte`,
    map_style:"Style de carte",
    style_standard:"Standard", style_satellite:"Satellite", style_terrain:"Relief", style_inat_density:"Densité d'échantillonnage iNaturalist",
    crit_ge:"Combler l'écart (groupes sous-représentés)",
    roads:"Routes", labels_places:"Étiquettes et lieux",
    vector_hint:"Fond vectoriel — activez les couches comme dans Maputnik. Les autres styles sont matriciels (routes intégrées).",
    inat_coverage:"Couverture iNaturalist",
    coverage_hint:`Où les données existent déjà (clair = bien échantillonné, foncé = lacunes) — une couche de densité iNaturalist « illuminer la carte » (Biodiversité Québec), pour le groupe actuel.`,
    getting_even:"Combler l'écart (groupes sous-représentés)",
    ge_hint:`Chaque cellule est colorée selon le groupe taxonomique le plus sous-représenté (oiseaux exclus — déjà bien couverts par eBird) — notre version à plus fine résolution du défi officiel « Combler l'écart ». Selon la densité d'observations iNaturalist : un échantillon, pas un inventaire.`,
    ge_info_btn:"Comment le groupe recommandé est-il déterminé ?",
    ge_method:`<b>Comment « quel groupe noter » est déterminé.</b> Touchez une cellule et l'application interroge iNaturalist, en direct, pour chaque groupe du vivant (oiseaux exclus — déjà couverts par eBird) :
      <ul>
      <li>combien d'espèces distinctes de qualité recherche sont notées <b style="color:var(--ink)">dans cette cellule</b>, par rapport à</li>
      <li>combien existent dans les <b style="color:var(--ink)">~50&nbsp;km autour</b>.</li>
      </ul>
      La couverture d'un groupe est la première divisée par la seconde, puis divisée par le taux d'enregistrement global de la cellule — un groupe moyen vaut donc <b style="color:var(--ink)">1</b>. Les groupes bien en dessous de 1 sont sous-documentés par rapport à leurs voisins et remontent en tête, <b style="color:var(--ink)">plus grande lacune relative d'abord</b>. Cette normalisation fait ressortir les vraies lacunes même là où tout est sous-documenté (une cellule ne peut contenir toute la diversité d'une région). La carte colore chaque cellule selon son groupe le plus faible ; touchez pour l'arbre complet classé, touchez un groupe pour le cartographier. Densité iNaturalist : un échantillon, pas un inventaire.`,
    ge_cats:["Poissons","Champignons","Reptiles et amphibiens","Invertébrés","Mammifères","Plantes"],
    ge_all:"Tous sous-échantillonnés",
    canada_only:"Canada seulement",
    canada_only_hint:"Masque les cellules au sud de la frontière, où la bande vive est une limite de données (la couche canadienne s'arrête à la frontière), pas une vraie lacune. Frontière approximative.",
    more_presets:"Plus ▾",
    fewer_presets:"Moins ▴",
    methodology_link:"Méthodologie complète et données — traçez chaque chiffre →",
    hide_panel:"Masquer le panneau (plus de carte)",
    show_panel:"Afficher le panneau",
    zoom_scale:"Recalibrer les couleurs sur la vue actuelle",
    zoom_scale_hint:`Classe les cellules par rapport à ce qui est à l'écran, pour faire ressortir les lacunes locales en zoomant. Désactivé = classement sur tout le Canada (une cellule foncée signifie la même chose partout).`,
    legend_rel:"Couleurs recalibrées sur cette vue — non comparables entre niveaux de zoom.",
    map_brightness:"Luminosité de la carte",
    data_opacity:"Opacité des données",
    how_scored:"Calcul de l'impact et sources de données",
    skip:"à éviter", go_here:"y aller",
    impact_expl:`Chaque objectif est noté <b style="color:var(--ink)">0–1 par cellule</b>; vos curseurs les combinent, puis les cellules sont <b style="color:var(--ink)">classées les unes par rapport aux autres</b> et affichées en <b style="color:var(--ink)">impact 0–100</b> (un centile — 100 = cellule la plus prioritaire affichée). Survolez une cellule pour voir les objectifs qui la motivent.`,
    axis_method:`<b style="color:var(--ink)">Calcul de chaque objectif (0–1 par cellule) :</b><br>• <b style="color:var(--ink)">Découvrir le plus d'espèces</b> — là où peu ont observé : densité d'observations iNaturalist inversée (Biodiversité Québec).<br>• <b style="color:var(--ink)">Trouver des espèces en péril</b> — espèces en péril observées à proximité, pondérées par statut (en voie de disparition 3 / menacée 2 / préoccupante 1) : évaluations COSEWIC/SARA (CAN-SAR) × occurrences GBIF.<br>• <b style="color:var(--ink)">Couvrir chaque habitat</b> — types de climat rarement observés : « surprise » climatique CHELSA.<br>• <b style="color:var(--ink)">Lacunes les plus fraîches</b> — beaucoup observé autrefois, peu récemment : densité iNaturalist récente vs historique.<br>• <b style="color:var(--ink)">Échantillonner avant qu'il soit trop tard</b> — changement d'habitat récent : perte de couvert forestier Hansen Global Forest Change.`,
    foot:`<b style="color:var(--ink)">Fonctionnement :</b> <a href="https://blitzthegap.org" target="_blank" rel="noopener" style="color:var(--acc)">Blitz the Gap</a> est un bioblitz pancanadien — rendez-vous dans un endroit prioritaire, notez ce que vous voyez sur <a href="https://www.inaturalist.org" target="_blank" rel="noopener" style="color:var(--acc)">iNaturalist</a>, et vos observations de qualité recherche alimentent le <a href="https://www.inaturalist.org/projects/blitz-the-gap-2026-general" target="_blank" rel="noopener" style="color:var(--acc)">projet 2026</a>, comblant les lacunes de la carte. Blitz the Gap est mené par le <a href="https://pollocklab.github.io/blitz-the-gap/" target="_blank" rel="noopener" style="color:var(--acc)">Pollock Lab</a> de l'Université McGill; ceci est un outil complémentaire en cours de développement, non officiel.<br><br>À l'échelle nationale, le signal de priorité robuste est la <b style="color:var(--ink)">sous-représentation</b> (densité iNaturalist) + la <b style="color:var(--ink)">couverture climatique</b> (CHELSA); la rareté et la fraîcheur sont maintenant réelles (rareté = espèces en péril COSEWIC/SARA via CAN-SAR + GBIF; fraîcheur = densité iNaturalist récente vs historique). Itinéraires auto/vélo/marche d'OSRM (FOSSGIS); temps de déplacement de Weiss 2018. CO₂ en voiture ≈ 0,18 kg/km; vélo/marche nul. Un outil de planification — masquez les lieux d'espèces sensibles et respectez la souveraineté des données autochtones avant toute diffusion publique.<br><br>Cette carte couvre de nombreux <b style="color:var(--ink)">territoires autochtones</b> — voyez lesquels sur <a href="https://native-land.ca" target="_blank" rel="noopener" style="color:var(--acc)">native-land.ca</a>, et obtenez le consentement avant d'observer sur leurs terres.`,
    top_cells:"Meilleures cellules (liste accessible)",
    view_explore:"Explorer", view_plan:"Planifier une sortie", view_compare:"Comparer les objectifs",
    loading:"Chargement de la carte…",
    load_error:"⚠ Impossible de charger la carte — vérifiez votre connexion.",
    retry:"↻ Réessayer",
    legend_title:"Où aller", legend_low:"bien échantillonné", legend_high:"plus grandes lacunes",
    density_low:"peu d'observations", density_high:"beaucoup d'observations", density_legend_hint:"observations iNaturalist (jusqu'à ~300 m)",
    axis_legend:[["bien échantillonné","plus grandes lacunes"],["aucune espèce en péril","beaucoup en péril"],["climat bien couvert","climat sous-échantillonné"],["observé récemment","en retard depuis longtemps"],["stable","changé récemment"]],
    legend_hint:`Plus foncé = priorité plus élevée. <span id="legendtap">Touchez une cellule pour voir quoi observer.</span>`,
    // dynamic
    legendtap_explore:"Touchez une cellule pour voir quoi observer.",
    legendtap_plan_start:"Touchez la carte pour définir votre départ.",
    legendtap_plan_dest:"Touchez la carte pour choisir votre destination.",
    near:"près de", here:"ici", my_loc_short:"ma position", my_area_ip:"ma région (selon l'IP)",
    locating:"localisation…", loc_unavail:"position indisponible — utilisez la recherche ci-dessus",
    cells_loading:t=>`Chargement de ${t}…`,
    prospects_where_start:"Près de votre départ", pop_go_title2:"Votre destination",
    prospects_lookup:"Recherche de ce qui vit ici…",
    prospects_none:"Aucune observation de qualité recherche ici pour l'instant — vous pourriez être la première personne à documenter ce qui vit ici.",
    prospects_err:"Impossible de charger les espèces pour l’instant — touchez la cellule à nouveau.",
    prospects_hd:(where,n,nearby)=>`<b style="color:var(--ink)">${where}</b> · ${n} espèces observées sur iNaturalist. À surveiller${nearby?' (à proximité)':''} :`,
    rare:"rare à proximité", gap:"lacune",
    here_count:n=>`${n} ici`, near_count:n=>`${n} dans ~40 km`, new_here:"nouveau ici", not_here_tag:"pas encore ici",
    inat_caveat:"Les nombres sont des observations iNaturalist — ce qui a été noté, pas un inventaire complet des espèces.",
    explore_all:"Tout explorer sur iNaturalist →", log_sighting:"Noter une observation", for_challenge:"pour ce défi",
    join:"se joindre →",
    more_challenges:"+ voir tous les défis officiels →",
    finding_routes:m=>`Recherche d'itinéraires ${m} réels…`,
    mode_why:(label,green)=>green?`${label} — le moyen le plus écolo d'atteindre une lacune dans votre temps.`:`${label} — la lacune la plus proche est trop loin pour la marche ou le vélo à temps.`,
    best_trips:(bud,lowc,est)=>`Meilleures sorties en ${bud} — le plus d'<b style="color:var(--ink)">impact × temps pour observer</b>${lowc?', par kg de CO₂':''}${est?' (certains temps estimés)':''} :`,
    or_skip:"Ou évitez le déplacement :",
    no_fit:(bud,mode)=>`Aucun aller-retour ne tient en ${bud} en ${mode} d'ici. Le mieux est d'observer là où vous êtes — ou d'aller plus loin avec plus de temps :`,
    farther:(bud,car)=>`Plus loin — au-delà de ${bud} d'aller-retour, mais à votre portée avec plus de temps${car?' ou en 🚗':''} :`,
    right_here:"Là où vous êtes",
    each_way:"par trajet", field:"terrain", in_field:"sur le terrain", round:"aller-retour", over:"au-delà de",
    no_extra:"aucun déplacement — observez la cellule où vous êtes déjà",
    pop_here_title:"Observez là où vous êtes",
    pop_here_sub:"vous êtes dans cette cellule de ~25 km — aucun déplacement requis",
    pop_here_foot:"consacrez votre temps à observer, pas à conduire jusqu'ici",
    pop_go_title:"Allez dans cette zone", pop_go_sub:"— n'importe où dans la cellule de ~25 km en surbrillance",
    pop_centre:"centre", pop_impact:"impact",
    pop_round:"aller-retour", pop_estimated:"(estimé)",
    pop_over_budget:bud=>`aller-retour — au-delà de votre ${bud}`,
    car_free:"sans voiture",
    table_caption:"40 meilleures cellules pour votre combinaison d'objectifs et votre groupe, du plus élevé au plus bas. Touchez une ligne pour l'ouvrir.",
    table_rank:"#", table_latlon:"Lat, lon", table_score:"Score",
    ins_hd:`<b>Le même lieu — objectifs différents, groupes différents.</b> Chaque carte teinte chaque cellule canadienne selon un objectif (<b>plus foncé = y aller</b>). Les zones chaudes changent selon l'objectif (un choix de valeurs) et selon le groupe (des espèces différentes comblent des lacunes différentes). Choisissez les lignes et colonnes; touchez une carte pour l'ouvrir dans le planificateur.`,
    ins_groups:"Groupes (lignes)", ins_goals:"Objectifs (colonnes)",
    ins_onhold:`<b>Comparaison en attente</b> — l'un des deux objectifs sélectionnés ne varie pas dans les groupes affichés, donc une corrélation n'a pas de sens ici. Choisissez-en deux qui varient.`,
    ins_spearman:(g1,g2,dis,verdict)=>`<b>${g1} vs ${g2}</b> — ρ de Spearman entre les classements des cellules (sur toutes les cellules; négatif = lieux opposés) : ${dis}. ${verdict}`,
    ins_v_all_diff:"Les deux objectifs pointent vers des <b>lieux différents dans chaque groupe affiché</b> — les cellules les plus sous-représentées ne sont pas là où sont les espèces en péril.",
    ins_v_agree:"Pour les groupes affichés, les deux objectifs <b>concordent</b> surtout ici.",
    ins_v_some:(neg,n)=>`Ils pointent vers des lieux différents dans <b>${neg} des ${n}</b> groupes affichés — sous-représentation et espèces en péril divergent souvent, mais pas toujours.`,
    obj_name:["Découvrir le plus d'espèces","Trouver des espèces en péril","Couvrir chaque habitat","Lacunes les plus fraîches","Échantillonner avant qu'il soit trop tard"],
    obj_q:["allez où peu de gens ont cherché","allez où se concentrent les espèces en péril (COSEWIC/SARA)","allez où le climat est sous-échantillonné","allez où l'on a beaucoup observé autrefois mais peu récemment (densité iNaturalist récente vs historique)","allez où le couvert forestier a été récemment perdu (coupe, feu, dépérissement)"],
    preset_name:["Lacune spatiale","Découverte d'espèces","Conservation"],
    preset_blurb:["Lieux sous-observés et climats sous-échantillonnés (densité iNaturalist + lacune climatique CHELSA).","Là où de nouvelles espèces pour le registre sont les plus probables : sous-représentation et cellules observées autrefois mais calmes récemment.","Là où se concentrent les espèces en péril, pondéré vers les habitats récemment modifiés (COSEWIC/SARA; CAN-SAR + GBIF)."],
    group:{Amphibia:"Amphibiens",Aves:"Oiseaux",Insecta:"Insectes",Mammalia:"Mammifères",Reptilia:"Reptiles",Plantae:"Plantes",Fungi:"Champignons",Actinopterygii:"Poissons",Arachnida:"Arachnides",Mollusca:"Mollusques","All biodiversity":"Toute la biodiversité"},
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
  document.querySelectorAll('[data-i18n-aria]').forEach(el=>{const v=t(el.getAttribute('data-i18n-aria'));if(v!=null)el.setAttribute('aria-label',v);});
  const lb=document.getElementById('langbtn');if(lb)lb.textContent=LANG.toUpperCase();   // #69: single toggle shows the active language
}
function relabelDynamic(){
  // re-render the JS-built widgets whose static strings come from the dict
  if(typeof rebuildTaxonOptions==='function')rebuildTaxonOptions();
  if(typeof rebuildObjs==='function')rebuildObjs();
  if(typeof rebuildCriteria==='function')rebuildCriteria();
  if(typeof rebuildStyles==='function')rebuildStyles();
  if(typeof rebuildModes==='function')rebuildModes();
  // re-apply the active preset to refresh language-dependent strings — but not while Getting Even is the active criterion
  if(typeof window.__activePreset==='number'&&!(typeof geActive==='function'&&geActive()))applyChallenge(PRESETS[window.__activePreset]);
  // budget label unit suffix
  if(typeof refreshBudget==='function')refreshBudget();
  // legend "tap" hint follows current view
  const lt=document.getElementById('legendtap');if(lt)lt.textContent=t(legendTapKey());
  // re-render Getting Even categorical legend in the new language (no-op when inactive)
  if(typeof updateLegend==='function'&&typeof geActive==='function'&&geActive())updateLegend();
  // start marker's tooltip/title/alt (created once at init) follow the language
  if(typeof startMarker!=='undefined'&&startMarker){const se=startMarker.getElement();if(se){se.setAttribute('title',t('start_tip'));se.setAttribute('aria-label',t('start_alt'));}}
  // start-place prospects idle text (only if untouched)
  const pr=document.getElementById('prospects');
  // Re-render whatever cell view is open in the new language. iNat responses are URL-cached and gap-tree
  // rows are cached, so this is zero-network. A cell tap lives in the popup (#popsp); the plan-mode
  // "around my start" list lives in the sidebar (#prospects).
  if(typeof fetchProspects!=='function'){}
  else if(_lastProspect&&_lastProspect.toPopup&&_lastProspect.spEl&&_lastProspect.spEl.isConnected){fetchProspects(_lastProspect.lat,_lastProspect.lon,_lastProspect.whereKey,{toPopup:true,spEl:_lastProspect.spEl,gapsEl:_lastProspect.gapsEl});}
  else if(pr&&pr.dataset.idle==='1')pr.innerHTML='<div class="hd" style="margin-top:10px" data-i18n="prospects_idle">'+t('prospects_idle')+'</div>';
  else if(pr&&_lastProspect)fetchProspects(_lastProspect.lat,_lastProspect.lon,_lastProspect.whereKey,{toPopup:false});
}
function setLang(l){if(l!==LANG){LANG=l;try{localStorage.setItem('wtb_lang',l);}catch(e){}}
  applyI18N();relabelDynamic();}

const DATA={}, DATA_DIR='cluster_results/ca/';
// Bounded fetch: abort after `ms` so a hung request can't leave the UI stuck (spinner / "finding routes…") forever.
function fetchT(url,ms){const c=new AbortController();const id=setTimeout(()=>c.abort(),ms||9000);return fetch(url,{signal:c.signal}).finally(()=>clearTimeout(id));}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
// Session cache of successful iNaturalist GETs, keyed by the exact URL (so it's scoped to a
// cell+taxon+box). These queries are idempotent within a session, so re-tapping a cell -- the
// common interaction -- costs zero network and paints instantly. Bounded + insertion-order
// evicted so a long pan across many cells can't grow it without limit. Failures are never
// cached, so a cell that errored retries fresh on the next tap.
const _jc=new Map(), JC_MAX=150;
function _jcSet(url,val){if(_jc.size>=JC_MAX)_jc.delete(_jc.keys().next().value);_jc.set(url,val);}
// Robust JSON GET: tapping a cell fans out ~12 iNaturalist calls at once, so under load the
// public API's latency tail breaches the abort and the odd 429 slips through. Without this a
// single slow/failed call silently blanked the gap tree. Retry once, honour Retry-After on
// 429/503, and only ever return parsed JSON (never a non-ok body that decodes to garbage).
async function jget(url,ms,tries){if(_jc.has(url))return _jc.get(url);ms=ms||9000;tries=tries||2;let last;
  for(let i=0;i<tries;i++){
    try{const r=await fetchT(url,ms);
      if((r.status===429||r.status===503)&&i<tries-1){const ra=Math.min(+(r.headers.get('retry-after'))||1,3);await sleep(ra*1000);last=new Error('HTTP '+r.status);continue;}
      if(!r.ok)throw new Error('HTTP '+r.status);
      const j=await r.json();_jcSet(url,j);return j;
    }catch(e){last=e;if(i<tries-1)await sleep(400*(i+1));}
  }
  throw last;
}
async function loadGroup(g){if(DATA[g])return;const r=await fetchT(DATA_DIR+FILES[g],20000);if(!r.ok)throw new Error('HTTP '+r.status+' loading '+FILES[g]);Object.assign(DATA,await r.json());}
// Getting Even: most under-represented taxonomic group per cell. CVD-safe Paul Tol Bright palette (index matches ge_cats order); -1 -> grey "all under-sampled".
const GE={}, GE_PAL=['#4477AA','#EE6677','#AA3377','#CCBB44','#66CCEE','#228833'], GE_ALL='#BBBBBB';
const gekey=(lat,lon)=>lat.toFixed(3)+','+lon.toFixed(3);
let GE_LOADED=false;
async function loadGE(){if(GE_LOADED)return;const r=await fetchT(DATA_DIR+'webapp_data_gettingeven.json',15000),j=await r.json();
  for(const e of j.gettingeven)GE[gekey(e[0],e[1])]=[e[2],e[3]];GE_LOADED=true;}
function geActive(){const e=document.getElementById('tgGettingEven');return!!(e&&e.checked);}
function zoomScaleActive(){const e=document.getElementById('tgZoomScale');return e?e.checked:true;}   // issue #20 removed the toggle; behaviour stays on by default
function caOnlyActive(){const e=document.getElementById('tgCanadaOnly');return e?e.checked:true;}        // issue #20 removed the toggle; behaviour stays on by default
// US-side cells (approx, lazy-loaded): hidden when "Canada only" is on -- the cross-border band is a data edge, not a real gap (#5).
let US_CELLS=null;
async function loadUS(){if(US_CELLS)return;try{const r=await fetchT(DATA_DIR+'us_cells.json',8000);US_CELLS=new Set((await r.json()).us_cells||[]);}catch(e){US_CELLS=new Set();}}
function applyCanadaMask(){if(!caOnlyActive())return;if(!US_CELLS){loadUS().then(recolour);return;}markers.forEach(m=>{if(US_CELLS.has(gekey(m.r[0],m.r[1])))setCellState(m.i,'rgba(0,0,0,0)',0);});}
function geColour(m){const v=GE[gekey(m.r[0],m.r[1])];if(!v)return GE_ALL;return v[0]<0?GE_ALL:(GE_PAL[v[0]]||GE_ALL);}
// A cell in the bottom band of the national gap ranking (e.g. well-sampled cities) is a low
// priority for the all-taxa mission -- but it's not worthless. Surface the two honest reasons
// to still record here: the single most under-recorded group (Getting Even), and the nearest
// cell that IS a strong gap. Never inflate the score to do it.
function exploreToPlan(lat,lon){if(!PLAN_ENABLED)return;setView('plan');setStart(lat,lon);planTrip();}
const IDX={discover:2,conservation:3,env:4,staleness:5,urgency:6}, TT=7, NTR=8;
const OSRM_BASE="https://routing.openstreetmap.de/";   // FOSSGIS public OSRM (car/bike/foot, CORS-enabled)
const MODES={Walk:{host:'routed-foot',kmh:5,emit:0,icon:'🚶'},Cycle:{host:'routed-bike',kmh:14,emit:0,icon:'🚲'},Drive:{host:'routed-car',kmh:60,emit:0.18,icon:'🚗'}};
const UNITS={Minutes:{toH:1/60,min:15,max:600,step:15,def:120},Hours:{toH:1,min:1,max:14,step:0.5,def:5},Days:{toH:24,min:1,max:21,step:1,def:2}};
const ROAD_FACTOR=1.35, MIN_FIELD_H=0.5, N_CANDIDATES=8;
function co2lbl(kg){return (kg<=0&&MODES[state.mode].emit===0)?t('car_free'):'~'+(kg<10?kg.toFixed(1):Math.round(kg))+' kg CO₂';}
const ICONIC={Amphibia:'Amphibia',Aves:'Aves',Insecta:'Insecta',Mammalia:'Mammalia',Reptilia:'Reptilia',Plantae:'Plantae',Fungi:'Fungi',Actinopterygii:'Actinopterygii',Arachnida:'Arachnida',Mollusca:'Mollusca'};
let prospectSeq=0;
let _lastProspect=null;   // {lat,lon,whereKey} of the open cell panel, so a language switch can re-render it
// iNaturalist returns common names per locale; ask for French names in FR mode (falls back to the
// scientific name where no French common name exists). EN is iNat's default, so leave en URLs
// untouched — keeps their cache keys and behaviour identical to before.
const inatLocale=()=>LANG==='fr'?'&locale=fr':'';
// whereKey is a stable sentinel ('around_start'|'right_here'|'destination'); the displayed
// heading is translated from it so the gap-popup test stays language-independent.
const WHERE_LBL={around_start:'prospects_where_start',right_here:'right_here',destination:'pop_go_title2'};
// iNaturalist taxon names/photos are user-editable -> untrusted. Escape before any HTML interpolation.
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeImg=u=>{u=String(u==null?'':u);return /^https:\/\//.test(u)?esc(u):'';};
// whereKey 'right_here'/'destination' from a CELL TAP render into the popup (opts.toPopup); 'around_start'
// (plan-mode "species around my start") still renders into the sidebar #prospects. Issue #44/#45: the
// gap tree and rare-species lists live in the popup now; fill-the-gap was dropped.
async function fetchProspects(lat,lon,whereKey,opts){
  const myseq=++prospectSeq;
  const toPopup=!!(opts&&opts.toPopup);
  // For a cell tap, paint into the persistent popup element refs passed by exploreCell, NOT a
  // getElementById lookup: Leaflet rebuilds the popup content node during the autoPan/setView churn
  // of a tap, so a queried node goes stale mid-fetch and the on-screen popup keeps loading (#56).
  const spEl=(opts&&opts.spEl)||null, gapsEl=(opts&&opts.gapsEl)||null;
  _lastProspect={lat,lon,whereKey,toPopup,spEl,gapsEl};   // remember the open panel so setLang can re-render it in the new language
  const where=t(WHERE_LBL[whereKey]||'here');
  const pr=toPopup?spEl:document.getElementById('prospects'); if(!pr) return;   // popup closed -> nothing to fill
  const msg=s=>toPopup?'<div class="popsec">'+s+'</div>':'<div class="hd">'+s+'</div>';   // .hd is dark-on-dark (sidebar); .popsec reads on the light popup
  if(!toPopup) pr.dataset.idle='0';
  pr.innerHTML=msg(t('prospects_lookup'));
  if(toPopup) fetchGapTree(lat,lon,gapsEl);   // the coverage-by-group tree only lives in the popup now
  const T_here=n=>t('here_count',n),T_near=n=>t('near_count',n);
  const L_rare=t('rare'),L_new=t('new_here'),L_notHere=t('not_here_tag');
  const ic=ICONIC[state.taxon]||'', HH=0.125;
  const q=(h)=>`https://api.inaturalist.org/v1/observations/species_counts?swlat=${lat-h}&nelat=${lat+h}&swlng=${lon-h}&nelng=${lon+h}&quality_grade=research&taxon_geoprivacy=open&threatened=false&per_page=500&order_by=count`+(ic?`&iconic_taxa=${ic}`:'')+inatLocale();
  try{
    const j=await jget(q(HH));
    if(myseq!==prospectSeq)return;
    const total=j.total_results||0;
    // Issue #48: rank by RECORDING VALUE, not global iNaturalist popularity. We always pull the ~40 km
    // neighbourhood so every candidate carries a regional count, which lets us prefer (a) species present
    // nearby but not yet in this cell (a new-to-cell record adds the most) and (b) species genuinely
    // uncommon *around here* (each record is more informative) — instead of sorting by global
    // observations_count, which is photo popularity, not value. Best-effort: if the neighbourhood call
    // fails, reg falls back to the local count and the ranking degrades to a within-cell order.
    const cell=(j.results||[]).filter(r=>r.count>=2 && r.taxon && r.taxon.default_photo);
    const k=await jget(q(3*HH)).catch(()=>null);
    if(myseq!==prospectSeq)return;
    const reg={}; ((k&&k.results)||[]).forEach(r=>{if(r.taxon)reg[r.taxon.id]=r.count;});
    const have=new Set(cell.map(r=>r.taxon.id));
    // new-to-cell: present in the ~40 km neighbourhood but not recorded in the cell itself
    const nearbyOnly=((k&&k.results)||[]).filter(r=>r.taxon&&r.taxon.default_photo&&r.count>=3&&!have.has(r.taxon.id))
      .map(r=>({count:0,taxon:r.taxon,_here:false,reg:r.count}));
    let res=cell.map(r=>({count:r.count,taxon:r.taxon,_here:true,reg:Math.max(r.count,reg[r.taxon.id]||0)})).concat(nearbyOnly);
    const nearby=nearbyOnly.length>0;
    if(!res.length){pr.innerHTML=msg(t('prospects_none'));return;}
    res=res.filter(r=>(r.taxon.observations_count||0)>40);                         // drop unverifiable global one-offs (data quality, not the ranking signal)
    // lexicographic priority: (1) new-to-cell before already-here; (2) bigger local gap first
    // (lower local/regional share); (3) tie-break by regional scarcity (fewer nearby records).
    const share=r=>r._here?(r.count/Math.max(1,r.reg)):0;
    res.sort((a,b)=>((a._here?1:0)-(b._here?1:0))||(share(a)-share(b))||(a.reg-b.reg));
    const badge=r=>r._here?(r.reg<=3?`<span class="rare">${L_rare}</span>`:''):`<span class="first">${L_new}</span>`;
    const ctline=r=>`${r._here?T_here(r.count):L_notHere} · ${T_near(r.reg)}`;
    if(toPopup){
      // Issue #48: the four highest-priority species to record here, side by side, no horizontal scroll.
      pr.innerHTML='<div class="popsec">'+t('pop_rare_hd')+'</div><div class="popgrid">'+res.slice(0,4).map(r=>{const tx=r.taxon;
        return `<a href="https://www.inaturalist.org/taxa/${tx.id}" target="_blank" rel="noopener" title="${esc(tx.name)}"><img src="${safeImg(tx.default_photo.square_url)}" loading="lazy" alt=""><div class="nm">${esc(tx.preferred_common_name||tx.name)}${badge(r)}</div><div class="ct">${ctline(r)}</div></a>`;}).join('')+'</div>';
      return;
    }
    res=res.slice(0,6);
    const ex=`https://www.inaturalist.org/observations?subview=map&swlat=${lat-HH}&nelat=${lat+HH}&swlng=${lon-HH}&nelng=${lon+HH}&quality_grade=research`;
    pr.innerHTML=`<div class="hd">${t('prospects_hd','<b style="color:var(--ink)">'+(where||t('here'))+'</b>',total.toLocaleString(LANG==='fr'?'fr-CA':'en-CA'),nearby)}</div>`+
      '<div class="prospects">'+res.map(r=>{const tx=r.taxon;
        return `<a class="sp" href="https://www.inaturalist.org/taxa/${tx.id}" target="_blank" rel="noopener" title="${esc(tx.name)}"><img src="${safeImg(tx.default_photo.square_url)}" loading="lazy" alt=""><div class="nm">${esc(tx.preferred_common_name||tx.name)}${badge(r)}</div><div class="ct">${ctline(r)}</div></a>`;}).join('')+'</div>'+
      `<div style="margin-top:8px;font-size:10.5px;color:var(--mut);line-height:1.35">${t('inat_caveat')}</div>`+
      `<div style="margin-top:7px;font-size:11px"><a href="${ex}" target="_blank" rel="noopener" style="color:var(--acc)">${t('explore_all')}</a> &nbsp;·&nbsp; <a href="https://www.inaturalist.org/observations/new" target="_blank" rel="noopener" style="color:var(--gd)">${t('log_sighting')}</a> &nbsp;·&nbsp; <a href="https://www.inaturalist.org/projects/${state.project}" target="_blank" rel="noopener" style="color:var(--mut)">${t('for_challenge')}</a></div>`;
  }catch(e){
    // Only the primary cell query (and any other un-isolated step) can land here -- the widening
    // top-up swallows its own transient errors, so a single tail abort or 429 no longer blanks an
    // otherwise-loadable cell. This IS a genuine load failure (not emptiness: empty cells render
    // prospects_none above), so honour the stale-seq guard and offer a retry.
    if(myseq!==prospectSeq)return;
    pr.innerHTML=msg(t('prospects_err')+' <a href="#" data-prospect-retry="1" style="color:var(--acc)">'+t('retry')+'</a>');
    const rl=pr.querySelector('[data-prospect-retry]'); if(rl)rl.onclick=ev=>{ev.preventDefault();fetchProspects(lat,lon,whereKey,opts);};
  }
}
const showProspects=debounce((lat,lon)=>fetchProspects(lat,lon,'around_start'),500);
// Per-cell taxonomic-coverage tree: distinct research-grade iNat species recorded in this cell
// per iconic group vs the ~50km neighbourhood. A group rich nearby but sparse here = a real
// recording gap. iconic_taxon_name == the app's group keys, so rows switch the map on click.
let gapSeq=0;
const GT_CACHE={};   // computed rows keyed by cell -- taxon-independent, so re-taps and taxon switches are instant and re-fire nothing
const gtkey=(lat,lon)=>lat.toFixed(3)+','+lon.toFixed(3);
// rows: [] -> too few nearby records to rank (honest, not a failure); null -> the lookup itself failed.
function paintGapTree(el,rows){
  if(rows===null){el.innerHTML='<div class="popsec">'+t('gaptree_err')+'</div>';return;}
  if(!rows.length){el.innerHTML='<div class="popsec">'+t('gaptree_sparse')+'</div>';return;}
  const lab=v=>v<0.2?t('gt_gap'):(v<0.6?t('gt_partial'):t('gt_ok'));
  const cls=v=>v<0.2?'gt-gap':(v<0.6?'gt-part':'gt-ok');
  // Issue #44: rows are sorted biggest-gap first; cap the popup at the four least-sampled groups, scroll for the rest.
  el.innerHTML='<div class="popsec">'+t('pop_groups_hd')+'</div>'+
    '<div class="popscroll"><div class="gaptree">'+rows.map(r=>{const pct=Math.round(Math.min(1,r.cov)*100);
      return `<button class="gtrow ${cls(r.cov)}" data-g="${esc(r.g)}" aria-label="${esc(groupName(r.g))}: ${t('gt_count',r.c,r.n)}, ${lab(r.cov)}. ${t('gt_switch',groupName(r.g))}"><span class="gtn">${esc(groupName(r.g))}</span><span class="gtbar"><span style="width:${pct}%"></span></span><span class="gtc">${t('gt_count',r.c,r.n)}</span></button>`;}).join('')+'</div></div>'+
    '<div class="popcap">'+t('gt_caveat')+'</div>';   // honest: these are iNaturalist record counts (a proxy), not true richness
  el.querySelectorAll('.gtrow').forEach(b=>b.onclick=()=>{const g=b.dataset.g;if(!FILES[g]||state.taxon===g)return;taxonSel.value=g;taxonSel.onchange();});
}
async function fetchGapTree(lat,lon,el){
  const myseq=++gapSeq;
  // `el` is the PERSISTENT popup element passed by exploreCell, not a getElementById lookup: Leaflet
  // rebuilds the popup content node during the autoPan/setView churn of a tap, so a queried node goes
  // stale mid-fetch and the on-screen popup keeps its loading text (issue #56).
  if(!el) return;   // popup container; closed -> nothing to fill
  const ck=gtkey(lat,lon);
  if(GT_CACHE[ck]!==undefined){paintGapTree(el,GT_CACHE[ck]);return;}
  el.innerHTML='<div class="popsec">'+t('gaptree_lookup')+'</div>';
  const HH=0.125,R=0.5,GR=t('group'),groups=Object.keys(ICONIC);
  const box=(h)=>`swlat=${lat-h}&nelat=${lat+h}&swlng=${lon-h}&nelng=${lon+h}`;
  const base='https://api.inaturalist.org/v1/observations/species_counts?quality_grade=research&taxon_geoprivacy=open&threatened=false&order_by=count';
  try{
    // Two calls total (issue #56): the cell numerator and the neighbourhood denominator are each ONE
    // species_counts page aggregated by group client-side. The old version made one total_results call
    // per iconic group (~10 extra requests on EVERY tap), which flooded iNaturalist's per-IP rate limit
    // during normal browsing and left the popup stuck on "Reading taxonomic coverage…". The neighbourhood
    // count is now a top-500 sample per box rather than exact per-group totals -- approximate for very
    // rich areas (a dense group can cap at its share of 500), fine as a coverage heuristic and revisitable
    // when the goal calculations are finalised (#49). Each leg swallows its own error.
    let cellFail=false,nbFail=false;
    const cellP=jget(`${base}&${box(HH)}&per_page=500`).catch(()=>{cellFail=true;return{results:[]};});
    const nbP=jget(`${base}&${box(R)}&per_page=500`).catch(()=>{nbFail=true;return{results:[]};});
    const [cj,nj]=await Promise.all([cellP,nbP]);
    if(myseq!==gapSeq)return;
    // total outage (both calls failed) -> "tap again", not a misleading "too few records here".
    if(cellFail&&nbFail){paintGapTree(el,null);return;}
    const cell={};(cj.results||[]).forEach(r=>{const g=r.taxon&&r.taxon.iconic_taxon_name;if(g)cell[g]=(cell[g]||0)+1;});
    const nbhd={};(nj.results||[]).forEach(r=>{const g=r.taxon&&r.taxon.iconic_taxon_name;if(g)nbhd[g]=(nbhd[g]||0)+1;});
    const elig=groups.filter(g=>nbhd[g]>=3&&GR[g]);
    let sumC=0,sumN=0;elig.forEach(g=>{sumC+=Math.min(cell[g]||0,nbhd[g]);sumN+=nbhd[g];});
    const rate=sumC>0?sumC/sumN:0;   // this cell's overall coverage rate; normalise so an avg group sits at 1
    const rows=elig.map(g=>{const c=Math.min(cell[g]||0,nbhd[g]);return{g,c,n:nbhd[g],cov:rate>0?(c/nbhd[g])/rate:0};}).sort((a,b)=>a.cov-b.cov);
    if(myseq!==gapSeq)return;
    GT_CACHE[ck]=rows;
    paintGapTree(el,rows);
  }catch(e){if(myseq===gapSeq)paintGapTree(el,null);}   // do not cache failures -- next tap retries
}

const RAMP=[[255,255,217],[237,248,177],[199,233,180],[127,205,187],[65,182,196],[29,145,192],[34,94,168],[37,52,148],[8,29,88]]; // YlGnBu (ColorBrewer, CVD-safe): pale yellow=well-sampled, dark navy=biggest gaps
function colour(t){t=Math.max(0,Math.min(1,t));const x=t*(RAMP.length-1),i=Math.floor(x),f=x-i;const a=RAMP[i],b=RAMP[Math.min(i+1,RAMP.length-1)];return `rgb(${Math.round(a[0]+(b[0]-a[0])*f)},${Math.round(a[1]+(b[1]-a[1])*f)},${Math.round(a[2]+(b[2]-a[2])*f)})`;}
function fmth(h){if(h>=24){const d=Math.floor(h/24),hr=Math.round(h%24);return d+'d'+(hr?(' '+hr+'h'):'');}const m=Math.round(h*60);return m>=60?Math.floor(m/60)+'h'+String(m%60).padStart(2,'0'):m+' min';}
function haversine(a,b,c,d){const R=6371,r=Math.PI/180,x=(c-a)*r,y=(d-b)*r,h=Math.sin(x/2)**2+Math.cos(a*r)*Math.cos(c*r)*Math.sin(y/2)**2;return 2*R*Math.asin(Math.sqrt(h));}

const ATTR='&copy; OpenStreetMap contributors · routing &copy; OSRM';
// MapLibre zoom = Leaflet zoom - 1 (256-px raster tiles): the old Leaflet zoom 4 maps to 3 here.
const map=new maplibregl.Map({container:'map',style:{version:8,sources:{},layers:[]},center:[-96,58],zoom:3,minZoom:1,maxZoom:18,attributionControl:false});
map.addControl(new maplibregl.AttributionControl({compact:true,customAttribution:ATTR}),'bottom-right');
map.addControl(new maplibregl.NavigationControl({showCompass:false}),'bottom-left');   // issue #54: clear of the floating panel
map.dragRotate.disable();map.touchZoomRotate.disableRotation();   // a 2-D priority map has no use for bearing/pitch
const CELL_LAYER='cells-fill';
// Layer stack bottom->top: basemap -> density (cov) -> cells-fill -> route lines; DOM markers sit above the canvas.
let _mapLoaded=false; const _onMap=[];
function whenMap(fn){_mapLoaded?fn():_onMap.push(fn);}
function firstNonBase(){const ls=map.getStyle().layers||[];const l=ls.find(x=>x.id!=='basemap');return l?l.id:undefined;}
map.on('load',()=>{
  _mapLoaded=true;
  map.addSource('cells',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
  // Per-cell colour/opacity ride on feature-state, so a taxon switch or recolour never reparses the 38k-cell
  // source -- it just restyles. fill-outline matches the fill so adjacent cells tile seamlessly (the old
  // canvas stroke-matches-fill trick, now native). #1/#44 colour logic stays in JS (colour()/geColour()).
  map.addLayer({id:CELL_LAYER,type:'fill',source:'cells',paint:{
    'fill-color':['coalesce',['feature-state','fc'],'rgba(0,0,0,0)'],
    'fill-opacity':['coalesce',['feature-state','op'],0],
    'fill-outline-color':['coalesce',['feature-state','fc'],'rgba(0,0,0,0)']}});
  map.on('click',CELL_LAYER,e=>{const f=e.features&&e.features[0];if(!f)return;const r=markers[f.id]&&markers[f.id].r;if(!r)return;
    if(caOnlyActive()&&US_CELLS&&US_CELLS.has(gekey(r[0],r[1])))return;state.view==='explore'?exploreCell(r[0],r[1]):planTap(r[0],r[1]);});
  map.on('mouseenter',CELL_LAYER,()=>{map.getCanvas().style.cursor='pointer';});
  map.on('mouseleave',CELL_LAYER,()=>{map.getCanvas().style.cursor='';});
  setBase('Standard');
  _onMap.forEach(fn=>{try{fn();}catch(e){}});_onMap.length=0;
});
// Esc / map-click popup close -> deselect (issue #16). MapLibre has no global popup events, so each popup we
// open is wired to onPopupOpen/onPopupClose and tracked in curPopup; map clicks auto-close (closeOnClick).
let _ppf=null,curPopup=null;
function onPopupOpen(){_ppf=document.activeElement;}
function onPopupClose(){try{if(_ppf&&_ppf.focus)_ppf.focus();}catch(e){}_ppf=null;curPopup=null;
  if(!_reselect&&state.view==='explore'&&(destMarker||destCell))clearSelection();}
function wirePopup(p){p.on('open',onPopupOpen);p.on('close',onPopupClose);curPopup=p;return p;}
function closePopup(){if(curPopup)curPopup.remove();}   // remove() fires 'close' -> onPopupClose
function setCellState(i,fc,op){if(_mapLoaded&&map.getSource('cells'))map.setFeatureState({source:'cells',id:i},{fc:fc,op:op});}
function lngLatBounds(latlngs){const b=new maplibregl.LngLatBounds();latlngs.forEach(p=>b.extend([p[1],p[0]]));return b;}
document.addEventListener('keydown',e=>{if(e.key!=='Escape')return;
  const hp=document.getElementById('howpanel');if(hp&&hp.classList.contains('open'))return;   // Esc closes the info panel first (handled below)
  if(state.view==='explore'&&(destMarker||destCell)){closePopup();clearSelection();}});
// Raster basemaps. Leaflet {s}/{r} templating isn't supported, so subdomains expand into a tiles[] array
// (round-robined) and {r} retina is dropped. The dead vector-basemap branch (#layertoggles, never wired to a
// real style) is gone with the maplibre-gl-leaflet shim.
const SUB=(u,s)=>s?s.split('').map(c=>u.replace('{s}',c)):[u];
const BASEMAPS={
  "Standard":{tiles:SUB('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png','abcd'),maxzoom:20},
  "Satellite":{tiles:SUB('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'),maxzoom:19},
  "Terrain":{tiles:SUB('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png','abc'),maxzoom:17},
  "Light":{tiles:SUB('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png','abcd'),maxzoom:20}   // base under the iNaturalist-density style; not a dropdown choice
};
let baseOpacity=1, covOpacity=0.8;   // covOpacity: density-overlay opacity, slider-driven when a data overlay is active (#39)
function setBase(name){
  if(!_mapLoaded){whenMap(()=>setBase(name));return;}
  if(map.getLayer('basemap'))map.removeLayer('basemap');
  if(map.getSource('basemap'))map.removeSource('basemap');
  const b=BASEMAPS[name];
  map.addSource('basemap',{type:'raster',tiles:b.tiles,tileSize:256,maxzoom:b.maxzoom,attribution:ATTR});
  map.addLayer({id:'basemap',type:'raster',source:'basemap',paint:{'raster-opacity':baseOpacity}},firstNonBase());
}

let markers=[], routeLine=null, destMarker=null, destCell=null, lastFit=null, lastScored=null, planned=false, lastDest=null;
const VAN=[49.28,-123.12];
const w0={}; OBJ.forEach((o,i)=>w0[o.key]=DEFAULT[i]);
const state={taxon:(FILES["All biodiversity"]?"All biodiversity":Object.keys(FILES)[0]), w:w0, start:VAN.slice(), budget:5, maxLeg:0, minRatio:0.5, unit:'Hours', lowc:false, mode:'Walk', modeSet:false, startProsp:false, view:'explore', startSet:false, planMode:'auto', project:'blitz-the-gap-2026-general'};
function debounce(fn,ms){let t;return(...a)=>{clearTimeout(t);t=setTimeout(()=>fn(...a),ms);};}
const replan=debounce(()=>{if(!planned)return;if(state.planMode==='dest'&&lastDest)setDest(lastDest[0],lastDest[1]);else planTrip();},650);

// divIcon -> a DOM element MapLibre anchors. Start = 18px dot anchored centre; dest = 26x38 pin anchored at its tip.
function iconEl(html){const d=document.createElement('div');d.style.lineHeight='0';d.innerHTML=html;return d;}
const startElHtml='<div style="width:18px;height:18px;border-radius:50%;background:rgb(139,168,132);border:3px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.45)"></div>';
const destElHtml='<svg width="26" height="38" viewBox="0 0 26 38"><path d="M13 0C6 0 0 6 0 13c0 9 13 25 13 25s13-16 13-25C26 6 20 0 13 0z" fill="#f0a000" stroke="#fff" stroke-width="2"/><circle cx="13" cy="13" r="5" fill="#fff"/></svg>';
function destMarkerAt(lat,lon){const el=iconEl(destElHtml);el.style.cursor='pointer';return new maplibregl.Marker({element:el,anchor:'bottom'}).setLngLat([lon,lat]);}
const startEl=iconEl(startElHtml);startEl.title=t('start_tip');startEl.setAttribute('aria-label',t('start_alt'));
const startMarker=new maplibregl.Marker({element:startEl,draggable:true,anchor:'center'}).setLngLat([state.start[1],state.start[0]]).addTo(map);
startMarker.on('drag',()=>{const ll=startMarker.getLngLat();state.start=[ll.lat,ll.lng];});
startMarker.on('dragend',()=>{const ll=startMarker.getLngLat();setStart(ll.lat,ll.lng);});
// Bare-map click: in MapLibre the cell-layer handler (above) already fired for clicks that hit a cell, so skip those.
map.on('click',e=>{if(_mapLoaded&&map.getLayer(CELL_LAYER)&&map.queryRenderedFeatures(e.point,{layers:[CELL_LAYER]}).length)return;
  state.view==='explore'?exploreCell(e.lngLat.lat,e.lngLat.lng):planTap(e.lngLat.lat,e.lngLat.lng);});
function setStartLabel(s){document.getElementById('startlbl').textContent=s;}
function setStart(lat,lon,tag){state.start=[lat,lon];startMarker.setLngLat([lon,lat]);state.startSet=true;refreshTapHint();
  setStartLabel((tag?tag+' · ':'')+lat.toFixed(3)+', '+lon.toFixed(3));
  geocode(lat,lon,tag); if(state.startProsp) showProspects(lat,lon); replan();}
// In Plan view the first interaction sets the start (your location is less flexible);
// once a start exists, every map tap re-picks the destination (the travel target is fully flexible).
function planTap(lat,lon){state.startSet?setDest(lat,lon):setStart(lat,lon);}
function legendTapKey(){if(state&&state.view==='plan')return state.startSet?'legendtap_plan_dest':'legendtap_plan_start';return 'legendtap_explore';}
function refreshTapHint(){const lt=document.getElementById('legendtap');if(lt)lt.textContent=t(legendTapKey());}
const geocode=debounce((lat,lon,tag)=>{
  fetchT(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=10&lat=${lat}&lon=${lon}`,7000)
    .then(r=>r.json()).then(j=>{if(state.start[0]!==lat||state.start[1]!==lon)return;   // start moved while geocoding -> stale label, skip
      const a=j.address||{};const nm=a.city||a.town||a.village||a.hamlet||a.county||a.state||((j.display_name||'').split(',')[0])||t('here');
      setStartLabel((tag?tag+' · ':'')+t('near')+' '+nm);}).catch(()=>{});},600);

function rows(){return DATA[state.taxon]||[];}
function impact(r){let s=0;for(const o of OBJ)s+=state.w[o.key]*(r[IDX[o.key]]||0);return s;}
function contribs(r){return OBJ.map((o,i)=>({nm:objName(i),c:state.w[o.key]*(r[IDX[o.key]]||0),raw:r[IDX[o.key]]||0})).filter(x=>x.c>0).sort((a,b)=>b.c-a.c);}
function contribStr(r){const c=contribs(r).slice(0,3).map(x=>x.nm.toLowerCase()+' '+(x.raw*100|0)+'/100'); return c.length?c.join(' · '):'—';}
// Plain-language reason this cell is worth a visit, from its single dominant (weighted) signal.
// Intrinsic-motivation framing; honest because it names the axis actually driving the score.

const HALF=0.125;   // half a 0.25-deg cell
function buildMarkers(){
  if(!_mapLoaded){whenMap(buildMarkers);return;}
  const rs=rows();
  // The 0.25° grid geometry is identical across taxa, so after the first build a taxon switch only swaps each
  // cell's data row + restyles via feature-state -- the 38k-feature source is never reparsed (setData runs once).
  if(markers.length===rs.length){markers.forEach((m,i)=>m.r=rs[i]);recolour();return;}
  const feats=rs.map((r,i)=>({type:'Feature',id:i,properties:{},geometry:{type:'Polygon',coordinates:[[
    [r[1]-HALF,r[0]-HALF],[r[1]+HALF,r[0]-HALF],[r[1]+HALF,r[0]+HALF],[r[1]-HALF,r[0]+HALF],[r[1]-HALF,r[0]-HALF]]]}}));
  map.getSource('cells').setData({type:'FeatureCollection',features:feats});
  markers=rs.map((r,i)=>({r:r,i:i,t:0}));   // r[0],r[1] invariant across taxa; i = stable feature id for setFeatureState
  recolour();
}
function renderCellTable(){
  const el=document.getElementById('celltable');if(!el)return;   // sr-only region: always populated for screen-reader users (issue #20 removed the visible list)
  const top=markers.map(m=>({r:m.r,t:m.t||0})).sort((a,b)=>b.t-a.t).slice(0,40);
  el.innerHTML='<table><caption>'+t('table_caption')+'</caption><thead><tr><th scope="col">'+t('table_rank')+'</th><th scope="col">'+t('table_latlon')+'</th><th scope="col">'+t('table_score')+'</th></tr></thead><tbody>'+
    top.map((o,i)=>`<tr tabindex="0" role="button" data-la="${o.r[0]}" data-lo="${o.r[1]}" aria-label="${t('rank_aria',i+1,o.r[0].toFixed(2),o.r[1].toFixed(2),(o.t*100|0))}"><td>${i+1}</td><td>${o.r[0].toFixed(2)}, ${o.r[1].toFixed(2)}</td><td>${(o.t*100|0)}/100</td></tr>`).join('')+'</tbody></table>';
  el.querySelectorAll('tr[data-la]').forEach(tr=>{const go=()=>{const la=+tr.dataset.la,lo=+tr.dataset.lo;map.jumpTo({center:[lo,la],zoom:8});state.view==='plan'?planTap(la,lo):exploreCell(la,lo);};tr.onclick=go;tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}};});
}
function recolour(){
  const cov=document.getElementById('tgCoverage')&&document.getElementById('tgCoverage').checked;
  if(geActive()){
    // Categorical layer: colour by most under-represented group (no magnitude). Flat opacity; stroke matches fill so cells tile seamlessly.
    markers.forEach(m=>{const c=geColour(m);setCellState(m.i,c,0.62);});
    applyCanadaMask();renderCellTable();return;
  }
  // Decouple SCORE from COLOUR. m.t is always the stable NATIONAL percentile -- it drives the
  // popup impact and the accessible Top-cells list, neither of
  // which should change as you pan. Viewport-relative ranking (when on) recolours ONLY the fill so
  // local gaps read at a glance, without redefining what a cell's score means (#1).
  const vals=markers.map(m=>impact(m.r));
  // Percentile-rank, not min-max: a few Arctic super-gaps (max discover + rare climate)
  // otherwise dominate the scale and crush every reachable cell to ~0/100. Rank spreads it evenly.
  const ord=vals.map((v,i)=>[v,i]).sort((a,b)=>a[0]-b[0]);const rank=new Array(vals.length);ord.forEach((p,k)=>rank[p[1]]=k);const n1=Math.max(vals.length-1,1);
  // If the chosen goal mix has no spatial signal (every cell scores the same), rank would paint
  // a fake index gradient -- show a flat map instead.
  const flat=ord.length>0&&(ord[ord.length-1][0]-ord[0][0])<1e-9;   // guard empty markers (recolour runs during init before data loads)
  markers.forEach((m,i)=>{m.t=flat?0:rank[i]/n1;});   // national score, stable across pan/zoom
  const rel=zoomScaleActive()&&!cov;
  const lr=document.getElementById('legendrel');if(lr)lr.style.display=rel?'block':'none';   // absent while the GE categorical legend is swapped in
  if(rel){
    // Colour = rank within the current viewport so a Montrealer zoomed in sees local gaps; off-view
    // cells go faint (they recolour on moveend). Hidden US cells excluded so border zooms aren't skewed.
    const inb=viewportCells();
    const dim=colour(0);markers.forEach(m=>setCellState(m.i,dim,.05));  // m.t (score) untouched
    fillViewport(inb);
    PREV_INB=new Set(inb);   // baseline for the incremental moveend path: everything else is already dim
  } else {
    PREV_INB=null;
    // Whole-grid paint: one feature-state write per cell (fc+op). fill-outline matches the fill so the 38k
    // cells tile seamlessly; no per-cell tooltip/handler (the single layer-level click handler covers all).
    markers.forEach(m=>{const ct=m.t,o=cov?0:0.25+0.5*ct,c=colour(ct);setCellState(m.i,c,o);});
  }
  applyCanadaMask();renderCellTable();
}
// Cells currently in view (US-side excluded when "Canada only" is on, so border zooms aren't skewed).
function viewportCells(){const b=map.getBounds();let inb=markers.filter(m=>b.contains([m.r[1],m.r[0]]));
  if(caOnlyActive()&&US_CELLS)inb=inb.filter(m=>!US_CELLS.has(gekey(m.r[0],m.r[1])));return inb;}
// Colour the in-bounds cells by their rank WITHIN the viewport (same formula as the full path). m.t untouched.
function fillViewport(inb){const vv=inb.map(m=>impact(m.r));
  const od=vv.map((v,i)=>[v,i]).sort((a,b)=>a[0]-b[0]);const rk=new Array(vv.length);od.forEach((p,k)=>rk[p[1]]=k);const m1=Math.max(vv.length-1,1);
  const fl=od.length>0&&(od[od.length-1][0]-od[0][0])<1e-9;
  inb.forEach((m,i)=>{const ct=fl?0:rk[i]/m1,o=.25+.5*ct,c=colour(ct);setCellState(m.i,c,o);});}
// In-bounds set from the last viewport-relative paint, so moveend only touches what changed (null = full repaint needed first).
let PREV_INB=null;
// Incremental moveend repaint: re-rank/re-colour only the in-view cells, dim only the cells that LEFT the
// viewport since the last paint, and leave the national score (m.t), out-of-view cells and the cell table alone.
function recolourViewport(){
  if(PREV_INB===null){recolour();return;}   // no baseline yet (e.g. first paint, or a non-rel path ran): do the full establish
  const inb=viewportCells(),now=new Set(inb);
  const dim=colour(0);
  PREV_INB.forEach(m=>{if(!now.has(m))setCellState(m.i,dim,.05);});  // cells that left view
  fillViewport(inb);
  PREV_INB=now;
  applyCanadaMask();   // US cells that scrolled into view stay hidden; far cheaper than a full re-rank
}
// Swap #maplegend between the priority ramp (default) and a categorical Getting Even legend. Pale swatches get a 1px #ccc border.
let GE_PRIORITY_LEGEND=null;
// Legend low/high labels follow the dominant goal so they stay accurate per preset
// (e.g. Conservation -> "no at-risk .. many at-risk", Species discovery -> "recently recorded .. long overdue").
function updateLegendLabels(){const ml=document.getElementById('maplegend');if(!ml||geActive())return;
  const cov=document.getElementById('tgCoverage');if(cov&&cov.checked)return;
  let bi=0,bv=-1;OBJ.forEach((o,i)=>{const v=state.w[o.key]||0;if(v>bv){bv=v;bi=i;}});
  const lab=(t('axis_legend')||[])[bi];const sp=ml.querySelectorAll('.lab span');
  if(lab&&sp.length>=2){sp[0].textContent=lab[0];sp[1].textContent=lab[1];}}
function updateLegend(){
  const ml=document.getElementById('maplegend');if(!ml)return;
  if(GE_PRIORITY_LEGEND===null)GE_PRIORITY_LEGEND=ml.innerHTML;   // capture the as-built priority legend once
  if(geActive()){
    const sw=(c,lab)=>`<div style="display:flex;align-items:center;gap:6px;margin:2px 0"><span style="width:13px;height:13px;border-radius:3px;background:${c};border:1px solid #ccc;flex:none"></span><span>${lab}</span></div>`;
    ml.innerHTML='<div class="lt">'+t('getting_even')+'</div>'+
      t('ge_cats').map((nm,i)=>sw(GE_PAL[i],nm)).join('')+sw(GE_ALL,t('ge_all'));
  }else if(document.getElementById('tgCoverage')&&document.getElementById('tgCoverage').checked){
    // The density overlay is a magma raster of iNaturalist record density, not the priority ramp — show its own scale.
    ml.innerHTML='<div class="ramp magma"></div><div class="lab"><span>'+t('density_low')+'</span><span>'+t('density_high')+'</span></div><div class="hint">'+t('density_legend_hint')+'</div>';
  }else{
    ml.innerHTML=GE_PRIORITY_LEGEND;updateLegendLabels();
    const lt=document.getElementById('legendtap');if(lt)lt.textContent=t(legendTapKey());
  }
}

// controls
const taxonSel=document.getElementById('taxon');
function rebuildTaxonOptions(){const cur=state.taxon;taxonSel.innerHTML='';
  Object.keys(FILES).forEach(k=>{const o=document.createElement('option');o.value=k;o.textContent=groupName(k);taxonSel.appendChild(o);});
  taxonSel.value=cur;}
rebuildTaxonOptions();
taxonSel.onchange=()=>{const prev=state.taxon;state.taxon=taxonSel.value;
  const pr=document.getElementById('prospects');if(pr){pr.dataset.idle='0';pr.innerHTML='<div class="hd">'+t('cells_loading',groupName(state.taxon))+'</div>';}
  if(typeof showLoading==='function')showLoading(true);
  loadGroup(state.taxon).then(()=>{buildMarkers();setCoverage();if(document.getElementById('insights').style.display==='block')renderInsights();})
    .catch(()=>{state.taxon=prev;taxonSel.value=prev;if(pr){pr.dataset.idle='1';pr.innerHTML='<div class="hd" style="margin-top:10px">'+t('prospects_idle')+'</div>';}})   // load failed: revert dropdown, don't leave a half-switched map
    .finally(()=>{if(typeof showLoading==='function')showLoading(false);});};

const objsDiv=document.getElementById('objs');
function rebuildObjs(){if(!objsDiv)return;objsDiv.innerHTML='';   // issue #20 removed the fine-tune sliders; presets are the criteria selector now
  OBJ.forEach((o,i)=>{const d=document.createElement('div');d.className='obj';
    d.innerHTML=`<div class="top"><span class="nm">${objName(i)}</span><span class="v" id="v_${o.key}">${state.w[o.key].toFixed(2)}</span></div>
      <div class="q">${objQ(i)}</div><input type="range" id="s_${o.key}" min="0" max="1" step="0.05" value="${state.w[o.key]}" aria-label="${objName(i)}">`;
    objsDiv.appendChild(d);
    d.querySelector('input').addEventListener('input',e=>{state.w[o.key]=parseFloat(e.target.value);
      document.getElementById('v_'+o.key).textContent=state.w[o.key].toFixed(2);markPreset(null);recolour();replan();});});}
rebuildObjs();
function applyWeights(arr,name){OBJ.forEach((o,i)=>{state.w[o.key]=arr[i];const s=document.getElementById('s_'+o.key),v=document.getElementById('v_'+o.key);if(s)s.value=arr[i];if(v)v.textContent=arr[i].toFixed(2);});markPreset(name);recolour();updateLegendLabels();replan();}
// Issue #20: criteria is a dropdown (was preset chips). Getting Even is one of the options;
// picking it forces "All biodiversity" (the layer is taxon-independent) and shows the GE map.
const critSel=document.getElementById('criteria');
function markPreset(name){if(!critSel||name===null)return;   // reflect the active preset in the dropdown
  const i=PRESETS.findIndex((_,k)=>presetName(k)===name);if(i>=0)critSel.value=String(i);}
function rebuildCriteria(){if(!critSel)return;const cur=critSel.value;critSel.innerHTML='';
  PRESETS.forEach((p,i)=>{const o=document.createElement('option');o.value=String(i);o.textContent=presetName(i);o.title=presetBlurb(i);critSel.appendChild(o);});
  const ge=document.createElement('option');ge.value='ge';ge.textContent=t('crit_ge');critSel.appendChild(ge);
  if(cur)critSel.value=cur;}
rebuildCriteria();
function applyChallenge(p){const i=PRESETS.indexOf(p);window.__activePreset=i;
  applyWeights(p.w,presetName(i));state.project=p.proj;
  const cb=document.getElementById('challengeBlurb');if(cb)cb.innerHTML=presetBlurb(i)+' <a href="https://www.inaturalist.org/projects/'+p.proj+'" target="_blank" rel="noopener" style="color:var(--acc);white-space:nowrap">'+t('join')+'</a>';}
if(critSel)critSel.onchange=()=>{const v=critSel.value, ge=document.getElementById('tgGettingEven');
  if(v==='ge'){
    if(state.taxon!=='All biodiversity'&&FILES['All biodiversity']){taxonSel.value='All biodiversity';taxonSel.onchange();}
    if(ge&&!ge.checked){ge.checked=true;ge.dispatchEvent(new Event('change'));}
  }else{
    if(ge&&ge.checked){ge.checked=false;ge.dispatchEvent(new Event('change'));}
    applyChallenge(PRESETS[+v]);
  }};
applyChallenge(PRESETS[0]);

const bmSel=document.getElementById('basemap');
// Issue #20: map style is one of four choices; the iNaturalist sampling-density overlay folds in as the 4th.
const MAP_STYLES=[['Standard','style_standard'],['Satellite','style_satellite'],['Terrain','style_terrain'],['__inat__','style_inat_density']];
function rebuildStyles(){const cur=bmSel.value||'Standard';bmSel.innerHTML='';MAP_STYLES.forEach(([v,k])=>{const o=document.createElement('option');o.value=v;o.textContent=t(k);bmSel.appendChild(o);});bmSel.value=cur;}
rebuildStyles();
bmSel.onchange=()=>{const v=bmSel.value, cov=document.getElementById('tgCoverage');
  if(v==='__inat__'){setBase('Light');if(cov&&!cov.checked){cov.checked=true;cov.dispatchEvent(new Event('change'));}}
  else{setBase(v);if(cov&&cov.checked){cov.checked=false;cov.dispatchEvent(new Event('change'));}}};
let covActive=false;
// dec25 100 m density is served as local raster PMTiles (issue #10): the magma colormap
// (rescale 0,10) is baked into transparent WEBP tiles so the overlay sharpens as you zoom
// to ~300 m, where the 1 km COG via TiTiler stayed coarse. Fungi has no dec25 layer ->
// keep its older 1 km COG on the remote tiler until one lands.
const COVPM=['All','Amphibia','Aves','Insecta','Mammalia','Plantae','Reptilia','Actinopterygii','Arachnida','Mollusca'];   // taxa with a local PMTiles (others -> All)
// Raster PMTiles now ride the native MapLibre pmtiles:// protocol (registered once) instead of a custom
// GridLayer that hand-decoded WEBP to canvas. The protocol reads each archive's TileJSON (native z9) and
// MapLibre overzooms past it; the magma alpha is baked in, so it composites straight over the basemap.
let _pmReg=false;
function ensurePM(){if(_pmReg)return;_pmReg=true;maplibregl.addProtocol('pmtiles',new pmtiles.Protocol().tile);}
function removeCov(){if(map.getLayer('cov'))map.removeLayer('cov');if(map.getSource('cov'))map.removeSource('cov');covActive=false;}
function setCoverage(){
  if(!_mapLoaded){whenMap(setCoverage);return;}
  removeCov();
  if(!document.getElementById('tgCoverage').checked){updateOpacityControl();return;}
  const ct=COVPM.includes(state.taxon)?state.taxon:'All';
  const attr='iNaturalist density &copy; Biodiversit\u00e9 Qu\u00e9bec';
  if(state.taxon==='Fungi'){   // no dec25 layer -> remote 1 km COG
    map.addSource('cov',{type:'raster',tiles:['https://tiler.biodiversite-quebec.ca/cog/tiles/{z}/{x}/{y}?url='+encodeURIComponent('https://object-arbutus.cloud.computecanada.ca/bq-io/io/inat_canada_heatmaps/Fungi_density_inat_1km.tif')+'&rescale=0,10&colormap_name=magma&resampling=cubic'],tileSize:256,maxzoom:14,attribution:attr});
  }else{
    ensurePM();
    map.addSource('cov',{type:'raster',url:'pmtiles://density/'+ct+'.pmtiles',tileSize:256,attribution:attr});
  }
  map.addLayer({id:'cov',type:'raster',source:'cov',paint:{'raster-opacity':covOpacity}},map.getLayer(CELL_LAYER)?CELL_LAYER:undefined);
  covActive=true;
  updateOpacityControl();
}
// #39: the opacity slider drives the data overlay when one is active (so it fades the magma to
// reveal road/water/place context), else it falls back to basemap brightness. Switches label,
// aria-label, slider value + % readout to match the active target.
function dataOverlayActive(){return covActive;}
function setBaseOpacity(v){baseOpacity=v;if(_mapLoaded&&map.getLayer('basemap'))map.setPaintProperty('basemap','raster-opacity',v);}
function setCovOpacity(v){covOpacity=v;if(_mapLoaded&&map.getLayer('cov'))map.setPaintProperty('cov','raster-opacity',v);}
function updateOpacityControl(){
  const sl=document.getElementById('baseop'),lab=document.getElementById('bople'),val=document.getElementById('bopv');
  if(!sl||!lab||!val)return;
  const ov=dataOverlayActive();
  const row=document.getElementById('opacityRow');
  if(row)row.style.display=ov?'':'none';   // #46(b): the slider only appears with a data overlay; lock basemap brightness at 100% otherwise
  if(!ov)setBaseOpacity(1);
  const v=ov?covOpacity:baseOpacity;
  sl.value=v;
  lab.textContent=t(ov?'data_opacity':'map_brightness');
  sl.setAttribute('aria-label',t(ov?'aria_data_opacity':'aria_map_brightness'));
  lab.setAttribute('data-i18n',ov?'data_opacity':'map_brightness');   // keep applyI18N repaint in sync with the active target
  sl.setAttribute('data-i18n-aria',ov?'aria_data_opacity':'aria_map_brightness');
  val.textContent=Math.round(v*100)+'%';
}
document.getElementById('tgCoverage').addEventListener('change',e=>{if(e.target.checked)document.getElementById('tgGettingEven').checked=false;updateLegend();setCoverage();recolour();
  if(!e.target.checked&&bmSel.value==='__inat__'){bmSel.value='Standard';setBase('Standard');}});   // coverage turned off elsewhere (e.g. GE) -> reset the style dropdown
document.getElementById('tgGettingEven').addEventListener('change',e=>{if(e.target.checked)document.getElementById('tgCoverage').checked=false;loadGE().then(()=>{setCoverage();updateLegend();recolour();});});
{const z=document.getElementById('tgZoomScale');if(z)z.addEventListener('change',recolour);}
{const c=document.getElementById('tgCanadaOnly');if(c)c.addEventListener('change',()=>{loadUS().then(recolour);});}
// Re-rank against the new viewport only while view-relative colouring is on (avoids the per-pan cost otherwise).
map.on('moveend',()=>{if(zoomScaleActive()&&!geActive())recolourViewport();});
// Issue #20: "How impact is scored & data sources" moved out of the sidebar to a floating map info button that expands a panel.
(function(){const b=document.getElementById('howbtn'),p=document.getElementById('howpanel'),x=document.getElementById('howclose');if(!b||!p)return;
  const set=open=>{p.classList.toggle('open',open);b.setAttribute('aria-expanded',open?'true':'false');};
  b.addEventListener('click',()=>set(!p.classList.contains('open')));
  if(x)x.addEventListener('click',()=>{set(false);b.focus();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&p.classList.contains('open')){set(false);b.focus();}});})();
// Collapse the side panel for a near-full-width map (Strava-clean) -- additive, reversible. invalidateSize after the layout settles.
(function(){const tog=document.getElementById('panelToggle');if(!tog)return;
  const setAria=c=>{const lab=t(c?'show_panel':'hide_panel');tog.setAttribute('aria-label',lab);tog.title=lab;tog.textContent=c?'›':'‹';};
  setAria(false);
  tog.addEventListener('click',()=>{const c=document.body.classList.toggle('panel-collapsed');setAria(c);setTimeout(()=>{try{map.resize();}catch(e){}},220);});})();
document.getElementById('baseop').addEventListener('input',e=>{const v=parseFloat(e.target.value);
  if(dataOverlayActive())setCovOpacity(v);   // #39: fade the magma data
  else setBaseOpacity(v);
  document.getElementById('bopv').textContent=Math.round(v*100)+'%';});
document.getElementById('maxleg').addEventListener('change',e=>{state.maxLeg=parseFloat(e.target.value);replan();});
document.getElementById('minratio').addEventListener('change',e=>{state.minRatio=parseFloat(e.target.value);replan();});

const budgetEl=document.getElementById('budget'), budvEl=document.getElementById('budv');
function unitLbl(u,v){return v+(u==='Minutes'?' min':u==='Days'?' d':'h');}
function refreshBudget(){const c=UNITS[state.unit],v=parseFloat(budgetEl.value);state.budget=v*c.toH;budvEl.textContent=unitLbl(state.unit,v);replan();}
document.getElementById('unit').addEventListener('change',e=>{state.unit=e.target.value;const c=UNITS[state.unit];budgetEl.min=c.min;budgetEl.max=c.max;budgetEl.step=c.step;budgetEl.value=c.def;refreshBudget();});
budgetEl.addEventListener('input',refreshBudget);
document.getElementById('lowc').addEventListener('change',e=>{state.lowc=e.target.checked;if(lastFit&&state.planMode!=='dest')rankAndRender();});
document.getElementById('startProsp').addEventListener('change',e=>{state.startProsp=e.target.checked; if(state.startProsp) fetchProspects(state.start[0],state.start[1],'around_start');});

const modesDiv=document.getElementById('modes');
function markModeBtn(){[...modesDiv.children].forEach(x=>x.classList.toggle('on',x.dataset.m===state.mode));}
function setModeWhy(html){const el=document.getElementById('modewhy');if(el)el.innerHTML=html;}
function rebuildModes(){modesDiv.innerHTML='';
  Object.keys(MODES).forEach(mn=>{const b=document.createElement('button');b.textContent=MODES[mn].icon+' '+modeName(mn);b.dataset.m=mn;
    b.onclick=()=>{state.mode=mn;state.modeSet=true;setModeWhy('');markModeBtn();replan();};   // an explicit pick is honoured; the auto-reason line clears
    modesDiv.appendChild(b);});
  markModeBtn();}
rebuildModes();
// The default mode is derived, not assumed: the greenest mode that can actually reach a real
// gap from the user's start within their time budget. Uses the cheap straight-line estimate
// (same predicate planTrip filters on) so it's instant and needs no routing call.
function estOneH(km,mode){return (km*ROAD_FACTOR)/MODES[mode].kmh;}
function greenestForAnyGap(){const [sl,so]=state.start,budget=state.budget,cap=state.maxLeg>0?state.maxLeg:Infinity;
  const kms=markers.filter(m=>impact(m.r)>0).map(m=>haversine(sl,so,m.r[0],m.r[1]));
  for(const mn of Object.keys(MODES))if(kms.some(km=>{const e=estOneH(km,mn);return 2*e<=budget-MIN_FIELD_H&&e<=cap*1.5;}))return mn;
  return 'Drive';}   // nothing greener reaches a gap in budget — driving is the honest fallback
function greenestForCell(km){const budget=state.budget;
  for(const mn of Object.keys(MODES))if(2*estOneH(km,mn)<=budget-MIN_FIELD_H)return mn;
  return 'Drive';}
function applyAutoMode(mode){state.mode=mode;markModeBtn();setModeWhy(t('mode_why',MODES[mode].icon+' '+modeName(mode),mode!=='Drive'));}

// type a town/address -> Nominatim forward geocoding (biased to BC/Canada), pick from a dropdown
const psEl=document.getElementById('placeSearch'), srEl=document.getElementById('searchResults');
function closeResults(){srEl.classList.remove('open');srEl.innerHTML='';psEl.setAttribute('aria-expanded','false');}
const doSearch=debounce(async q=>{
  if(q.trim().length<3){closeResults();return;}
  try{
    const u=`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&countrycodes=ca&viewbox=-141,84,-52,41&q=${encodeURIComponent(q)}`;
    const r=await fetch(u,{headers:{'Accept-Language':'en'}}).then(r=>r.json());
    if(psEl.value.trim()!==q.trim())return;                       // stale
    if(!r.length){const d=document.createElement('div');d.className='res';d.style.cssText='color:var(--mut);cursor:default';d.textContent='No places found';srEl.replaceChildren(d);psEl.setAttribute('aria-expanded','true');srEl.classList.add('open');return;}
    srEl.replaceChildren(...r.map(p=>{const parts=p.display_name.split(', '),head=parts[0],sub=parts.slice(1,4).join(', ');
      const d=document.createElement('div');d.className='res';d.setAttribute('role','option');d.dataset.lat=p.lat;d.dataset.lon=p.lon;
      const b=document.createElement('b');b.textContent=head;const sb=document.createElement('div');sb.className='sub';sb.textContent=sub;d.append(b,sb);
      d.onclick=()=>{map.jumpTo({center:[+p.lon,+p.lat],zoom:9});setStart(+p.lat,+p.lon,head);psEl.value=head;closeResults();};
      return d;}));
    psEl.setAttribute('aria-expanded','true');srEl.classList.add('open');
  }catch(e){closeResults();}
},450);
psEl.addEventListener('input',e=>doSearch(e.target.value));
psEl.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();const f=srEl.querySelector('.res[data-lat]');if(f)f.click();}else if(e.key==='Escape')closeResults();});
document.addEventListener('click',e=>{if(!srEl.contains(e.target)&&e.target!==psEl)closeResults();});
function locateMe(){setStartLabel(t('locating'));
  let done=false;
  const ok=(lat,lon,how)=>{if(done)return;done=true;map.jumpTo({center:[lon,lat],zoom:7});setStart(lat,lon,how);};   // idempotent: GPS resolving after the IP fallback fired can't double-set
  const fail=()=>{if(!done)setStartLabel(t('loc_unavail'));};                                            // never clobber a successful locate
  if(navigator.geolocation){navigator.geolocation.getCurrentPosition(p=>ok(p.coords.latitude,p.coords.longitude,t('my_loc_short')),()=>{if(!done)ipLoc(ok,fail);},{timeout:6000});setTimeout(()=>{if(!done)ipLoc(ok,fail);},6500);}else ipLoc(ok,fail);}
document.getElementById('setMe').onclick=locateMe;
function ipLoc(ok,fail){fetchT('https://ipapi.co/json/',6000).then(r=>r.json()).then(j=>{if(j&&j.latitude)ok(j.latitude,j.longitude,t('my_area_ip'));else if(fail)fail();}).catch(()=>{if(fail)fail();});}

// the trip planner
document.getElementById('plan').onclick=async()=>{await planTrip();const t=document.getElementById('trips');if(t)t.scrollIntoView({behavior:'smooth',block:'start'});};
async function planTrip(){
  planned=true;state.planMode='auto';
  if(!state.modeSet)applyAutoMode(greenestForAnyGap());
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
    try{const u=`${OSRM_BASE}${M.host}/route/v1/driving/${slon},${slat};${c.m.r[1]},${c.m.r[0]}?overview=full&geometries=geojson`;const j=await fetchT(u).then(r=>r.json());
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
// Route to a destination the user tapped (vs planTrip, which auto-suggests the best gaps).
// Snaps to the gap cell under the tap, routes from the (fixed) start, and shows it.
async function setDest(lat,lon){
  planned=true;state.planMode='dest';lastDest=[lat,lon];
  let best=markers[0],bd=1e9;for(const m of markers){const d=Math.abs(m.r[0]-lat)+Math.abs(m.r[1]-lon);if(d<bd){bd=d;best=m;}}
  if(!best)return;
  const [slat,slon]=state.start,budget=state.budget;
  const km=haversine(slat,slon,best.r[0],best.r[1]);
  if(!state.modeSet)applyAutoMode(greenestForCell(km));   // greenest mode that fits THIS trip in budget
  const M=MODES[state.mode];
  const trips=document.getElementById('trips');
  if(trips)trips.innerHTML='<div class="hd">'+t('finding_routes',modeName(state.mode).toLowerCase())+'</div>';
  let oneH=(km*ROAD_FACTOR)/M.kmh,oneKm=km*ROAD_FACTOR,geo=null,real=false;
  try{const u=`${OSRM_BASE}${M.host}/route/v1/driving/${slon},${slat};${best.r[1]},${best.r[0]}?overview=full&geometries=geojson`;
    const j=await fetchT(u).then(r=>r.json());
    if(j.code==='Ok'&&j.routes&&j.routes[0]){const rt=j.routes[0];oneH=rt.duration/3600;oneKm=rt.distance/1000;geo=rt.geometry;real=true;}}catch(e){}
  const o={m:best,km,imp:impact(best.r),oneH,oneKm,geo,real,fieldH:budget-2*oneH,co2:2*oneKm*M.emit};
  if(trips){trips.innerHTML='<div class="hd">'+t('pop_go_title2')+'</div>';tripRow(trips,o,'d',0);}
  selectTrip(o);
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
// Trip-planner overlays as MapLibre sources/layers (were Leaflet rectangle/polyline/featureGroup vectors).
// destCell = the dashed green target square; route = the white-cased green line(s). DOM markers ride on top.
let routeIds=[];
function setDestCell(lat,lon){if(!_mapLoaded)return;removeDestCell();const H=0.125;
  map.addSource('destcell',{type:'geojson',data:{type:'Feature',geometry:{type:'Polygon',coordinates:[[[lon-H,lat-H],[lon+H,lat-H],[lon+H,lat+H],[lon-H,lat+H],[lon-H,lat-H]]]}}});
  map.addLayer({id:'destcell-fill',type:'fill',source:'destcell',paint:{'fill-color':'#74c476','fill-opacity':0.16}});
  map.addLayer({id:'destcell-line',type:'line',source:'destcell',paint:{'line-color':'#1b7837','line-width':2,'line-dasharray':[2.5,2.5]}});
  destCell=true;}
function removeDestCell(){if(_mapLoaded){['destcell-fill','destcell-line'].forEach(id=>{if(map.getLayer(id))map.removeLayer(id);});if(map.getSource('destcell'))map.removeSource('destcell');}destCell=null;}
function addRoute(coords,paint){if(!_mapLoaded)return;const id='rt'+routeIds.length;
  map.addSource(id,{type:'geojson',data:{type:'Feature',geometry:{type:'LineString',coordinates:coords.map(c=>[c[1],c[0]])}}});
  const p={'line-color':paint.color,'line-width':paint.weight,'line-opacity':paint.opacity};if(paint.dash)p['line-dasharray']=paint.dash;
  map.addLayer({id:id,type:'line',source:id,layout:{'line-cap':'round','line-join':'round'},paint:p});routeIds.push(id);routeLine=true;}
function clearRouteLines(){if(_mapLoaded)routeIds.forEach(id=>{if(map.getLayer(id))map.removeLayer(id);if(map.getSource(id))map.removeSource(id);});routeIds=[];routeLine=null;}
function clearRoute(){clearRouteLines();removeDestCell();const dm=destMarker;destMarker=null;destCell=null;routeLine=null;if(dm)dm.remove();}
// Issue #16: deselect a cell and return to the "nothing selected" home state without editing the URL.
let _reselect=false;   // true only while exploreCell swaps one selection for another (suppress the close-driven clear)
function clearSelection(){clearRoute();
  const pr=document.getElementById('prospects');if(pr){pr.dataset.idle='1';pr.innerHTML='<div class="hd" style="margin-top:10px" data-i18n="prospects_idle">'+t('prospects_idle')+'</div>';}
  _lastProspect=null;   // the cell popup (which held the coverage tree + species) is gone; nothing for setLang to re-render
  try{history.replaceState(null,'',location.pathname);}catch(e){}}
function openDestPopup(lat,lon,html){const p=wirePopup(new maplibregl.Popup({offset:30,maxWidth:'340px'}).setHTML(html));
  destMarker=destMarkerAt(lat,lon).addTo(map);destMarker.setPopup(p);destMarker.togglePopup();}
function selectTrip(o){clearRoute();const dest=[o.m.r[0],o.m.r[1]];
  setDestCell(dest[0],dest[1]);
  if(o.here){
    map.fitBounds([[dest[1]-0.125,dest[0]-0.125],[dest[1]+0.125,dest[0]+0.125]],{padding:90,maxZoom:10});
    openDestPopup(dest[0],dest[1],`<b>${t('pop_here_title')}</b><br><span style="color:#667">${t('pop_here_sub')}</span><br>${t('pop_impact')} <b>${(o.m.t*100|0)}/100</b> · ${contribStr(o.m.r)}<br>${t('pop_here_foot')}`);
    document.querySelectorAll('#trips .row').forEach(el=>el.classList.toggle('sel',el.dataset.id===o.id));
    fetchProspects(dest[0],dest[1],'right_here');return;
  }
  let pts=[state.start,dest];
  if(o.geo){
    // OSRM snaps the route ends to the nearest road, so its geometry starts/ends
    // a little off the actual start pin and cell centre. Draw the routed road solid,
    // and bridge the off-road hops (pin->road, road->cell) with a faint dashed link
    // so the line always visibly connects to where you are and where you're going.
    const cs=o.geo.coordinates.map(c=>[c[1],c[0]]);
    addRoute(cs,{color:'#ffffff',weight:9,opacity:.95});
    addRoute(cs,{color:'rgb(139,168,132)',weight:4.5,opacity:1});
    addRoute([state.start,cs[0]],{color:'rgb(139,168,132)',weight:2.5,dash:[0.8,2.4],opacity:.8});
    addRoute([cs[cs.length-1],dest],{color:'rgb(139,168,132)',weight:2.5,dash:[0.8,2.4],opacity:.8});
    pts=[state.start,...cs,dest];
  } else {
    addRoute([state.start,dest],{color:'#ffffff',weight:8,opacity:.95});
    addRoute([state.start,dest],{color:'rgb(139,168,132)',weight:4,dash:[1.75,1.75],opacity:1});
  }
  map.fitBounds(lngLatBounds(pts),{padding:60,maxZoom:9});
  openDestPopup(dest[0],dest[1],`<b>${t('pop_go_title')}</b> <span style="color:#667">${t('pop_go_sub')}</span><br><span style="color:#667">${t('pop_centre')} ${dest[0].toFixed(2)}, ${dest[1].toFixed(2)}</span><br>${t('pop_impact')} <b>${(o.m.t*100|0)}/100</b> · ${contribStr(o.m.r)}<br>${o.fieldH>=MIN_FIELD_H?`${MODES[state.mode].icon} ${fmth(o.oneH)} ${t('each_way')} · ${fmth(o.fieldH)} ${t('in_field')}`:`${MODES[state.mode].icon} ${fmth(o.oneH)} ${t('each_way')} · ${fmth(2*o.oneH)} ${t('pop_over_budget',budvEl.textContent)}`}<br>${co2lbl(o.co2)} ${t('pop_round')}${o.real?'':' '+t('pop_estimated')}`);
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
  const vals=rows.map(r=>r[2+gi]);let lo=Infinity,hi=-Infinity;for(const v of vals){if(v<lo)lo=v;if(v>hi)hi=v;}const rng=(hi-lo)||1;
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
  rowsTaxa.forEach(tx=>{html+=`<div class="rl">${groupName(tx)}</div>`;goalsOn.forEach(gi=>html+=`<div class="cell" tabindex="0" role="button" aria-label="${groupName(tx)}, ${objName(gi)}" data-tx="${tx}" data-gl="${gi}"><canvas width="220" height="170" role="img" aria-label="${t('prio_map_aria',groupName(tx),objName(gi))}"></canvas></div>`);});
  html+='</div><div class="idis" id="idis"></div>';
  ins.innerHTML=html;
  ins.querySelectorAll('.cell').forEach(c=>{drawMini(c.querySelector('canvas'),DATA[c.dataset.tx],+c.dataset.gl);
    c.onclick=()=>{const tx=c.dataset.tx,gi=+c.dataset.gl;taxonSel.value=tx;state.taxon=tx;loadGroup(tx).then(()=>{buildMarkers();applyWeights(OBJ.map((_,i)=>i===gi?1:0),objName(gi));setView('plan');});};c.onkeydown=ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();c.click();}};});
  ins.querySelectorAll('.chip[data-tx]').forEach(ch=>{ch.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();ch.click();}};ch.onclick=()=>{const t=ch.dataset.tx,i=state.insTaxa.indexOf(t);if(i>=0){if(state.insTaxa.length>1)state.insTaxa.splice(i,1);}else state.insTaxa.push(t);renderInsights();};});
  ins.querySelectorAll('.chip[data-gl]').forEach(ch=>{ch.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();ch.click();}};ch.onclick=()=>{const g=+ch.dataset.gl,i=state.insGoals.indexOf(g);if(i>=0){if(state.insGoals.length>1)state.insGoals.splice(i,1);}else state.insGoals.push(g);state.insGoals.sort((x,y)=>x-y);renderInsights();};});
  // Correlate the FIRST TWO selected goals across cells. Every axis is real now, so any pair works
  // -- the comparison follows the user's goal selection instead of being hardcoded to two axes.
  const gs=state.insGoals; if(gs.length<2){document.getElementById('idis').innerHTML='';return;}
  const gi=gs[0],gj=gs[1],ci=2+gi,cj=2+gj;
  const varies=(b,c)=>{let mn=1e9,mx=-1e9;for(const r of b){if(r[c]<mn)mn=r[c];if(r[c]>mx)mx=r[c];}return mx>mn;};
  const disVals=rowsTaxa.map(tx=>{const b=DATA[tx];return (varies(b,ci)&&varies(b,cj))?{tx,rho:spear(b.map(r=>r[ci]),b.map(r=>r[cj]))}:null;}).filter(Boolean);
  if(!disVals.length){document.getElementById('idis').innerHTML=t('ins_onhold');return;}
  const dis=disVals.map(d=>`${groupName(d.tx)} <b>${d.rho.toFixed(2)}</b>`).join(' · ');
  const neg=disVals.filter(d=>d.rho<0).length, n=disVals.length;   // verdict follows the actual signs
  const verdict = neg===n ? t('ins_v_all_diff') : neg===0 ? t('ins_v_agree') : t('ins_v_some',neg,n);
  document.getElementById('idis').innerHTML=t('ins_spearman',objName(gi),objName(gj),dis,verdict);
}
// Explore mode: tap a cell to see its score + what to record there (no trip planning).
function exploreCell(lat,lon){
  if(!markers.length)return;   // map tapped before cell data loaded
  let best=markers[0],bd=1e9;for(const m of markers){const d=Math.abs(m.r[0]-lat)+Math.abs(m.r[1]-lon);if(d<bd){bd=d;best=m;}}
  // #72: a tap snaps to the nearest cell. Reject taps far from any cell (no grabbing a distant border cell
  // from mid-ocean / outside the grid; ~0.45° Manhattan ≈ within one 0.25° cell), and never select a hidden
  // US cell — the marker handler already guards direct cell taps, this covers taps on the bare map.
  if(bd>0.45||(caOnlyActive()&&US_CELLS&&US_CELLS.has(gekey(best.r[0],best.r[1]))))return;
  _reselect=true;   // swapping selections: suppress the close-driven deselect until the new popup is open (issue #16)
  const o=best,dest=[o.r[0],o.r[1]];clearRoute();
  try{history.replaceState(null,'','?at='+dest[0].toFixed(3)+','+dest[1].toFixed(3)+'&g='+encodeURIComponent(state.taxon));}catch(e){}
  setDestCell(dest[0],dest[1]);
  // Issue #44/#56: the popup holds the per-cell coverage tree and the rarest species, filled async. setDOMContent
  // keeps the same persistent element (the two child containers stay attached) so an in-flight fetch can fill it.
  const popDiv=document.createElement('div');
  const popGaps=document.createElement('div'); popGaps.id='popgaps'; popGaps.innerHTML='<div class="popsec">'+t('gaptree_lookup')+'</div>';
  const popSp=document.createElement('div'); popSp.id='popsp'; popSp.innerHTML='<div class="popsec">'+t('prospects_lookup')+'</div>';
  popDiv.appendChild(popGaps); popDiv.appendChild(popSp);
  const pop=wirePopup(new maplibregl.Popup({offset:30,maxWidth:'320px'}).setDOMContent(popDiv));
  destMarker=destMarkerAt(dest[0],dest[1]).addTo(map);destMarker.setPopup(pop);
  // #70: place the tapped cell low (≈1/3 of the map height below centre) so the upward-opening popup
  // clears the top chrome on laptops — true-centering hid the popup's top. MapLibre popups don't auto-pan.
  const _z=map.getZoom(),_dp=map.project([dest[1],dest[0]]),_mh=map.getContainer().clientHeight;
  map.jumpTo({center:map.unproject([_dp.x,_dp.y-_mh/3]),zoom:_z});
  destMarker.togglePopup();
  _reselect=false;
  fetchProspects(dest[0],dest[1],'destination',{toPopup:true,spEl:popSp,gapsEl:popGaps});
}
function setView(v){
  state.view=v;
  document.getElementById('insights').style.display=v==='compare'?'block':'none';
  const ml=document.getElementById('maplegend');if(ml)ml.style.display=v==='compare'?'none':'block';
  const lt=document.getElementById('legendtap');if(lt)lt.textContent=t(legendTapKey());
  document.getElementById('tripui').style.display=v==='plan'?'':'none';
  for(const [id,vv] of [['vexplore','explore'],['vplan','plan'],['vcompare','compare']]){const b=document.getElementById(id);b.classList.toggle('on',v===vv);b.setAttribute('aria-pressed',v===vv);}
  if(v!=='plan')clearRoute();
  // #40: the side panel isn't shown over Compare goals, so hide its collapse toggle there; restore it for Explore/Plan.
  const ptog=document.getElementById('panelToggle');if(ptog)ptog.style.display=v==='compare'?'none':'';
  if(v==='compare')renderInsights(); else map.resize();
}
document.getElementById('vexplore').onclick=()=>setView('explore');
document.getElementById('vplan').onclick=()=>setView('plan');
document.getElementById('vcompare').onclick=()=>setView('compare');
if(!PLAN_ENABLED){const vp=document.getElementById('vplan');if(vp)vp.style.display='none';const tu=document.getElementById('tripui');if(tu)tu.style.display='none';}   // issue #17
if(!COMPARE_ENABLED){const vc=document.getElementById('vcompare');if(vc)vc.style.display='none';}   // issue #71
if(!PLAN_ENABLED&&!COMPARE_ENABLED){const vt=document.getElementById('viewtoggle');if(vt)vt.style.display='none';}   // only Explore remains — the toggle is redundant

function showLoading(on){const el=document.getElementById('loading');if(el)el.style.display=on?'block':'none';}

// language toggle: seed from navigator.language / localStorage (done above), wire buttons, apply chrome
document.getElementById('langbtn').onclick=()=>setLang(LANG==='en'?'fr':'en');   // #69: one button toggles EN/FR
applyI18N();   // paint static chrome + set <html lang> for the seeded language

function bootSeq(){return loadGroup(state.taxon).then(()=>{buildMarkers();setView('explore');showLoading(false);
  try{const u=new URLSearchParams(location.search),at=u.get('at'),g=u.get('g');
    const go=()=>{if(at){const p=at.split(',').map(Number);if(p.length===2&&isFinite(p[0])&&isFinite(p[1])&&p[0]>=BB.minlat&&p[0]<=BB.maxlat&&p[1]>=BB.minlon&&p[1]<=BB.maxlon){map.jumpTo({center:[p[1],p[0]],zoom:7});exploreCell(p[0],p[1]);}}};
    if(g&&FILES[g]&&g!==state.taxon){state.taxon=g;taxonSel.value=g;loadGroup(g).then(()=>{buildMarkers();go();}).catch(()=>{});}else{go();}
    if(!at)locateMe();   // default Explore to the user's location (no shared cell to restore)
  }catch(e){}}).catch(()=>{const el=document.getElementById('loading');if(el){el.style.display='block';el.innerHTML=t('load_error')+' <a href="#" onclick="bootRetry();return false;" style="color:#7fd1ff">'+t('retry')+'</a>';}});}
function bootRetry(){const el=document.getElementById('loading');if(el){el.style.display='block';el.textContent=t('loading');}bootSeq();}
bootSeq();
</script></body></html>"""

out = (HTML.replace("__FILES__", json.dumps(FILES, separators=(",", ":")))
           .replace("__OBJ__", json.dumps(OBJ))
           .replace("__PRESETS__", json.dumps(PRESETS))
           .replace("__DEFAULT__", json.dumps(DEFAULT))
           .replace("__PLAN_ENABLED__", "true" if PLAN_ENABLED else "false")
           .replace("__COMPARE_ENABLED__", "true" if COMPARE_ENABLED else "false"))
open("index.html", "w").write(out)
print("wrote index.html  ({:.0f} KB)".format(len(out) / 1024))
