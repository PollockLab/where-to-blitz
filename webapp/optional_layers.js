// Optional data products are build-validated and pinned to exact source bytes.
// These helpers stay executable without the map for cross-language parity tests.
const OptionalData={
  viewport(west,south,east,north){
    const wrap=x=>((x+180)%360+360)%360-180;
    return east-west>=360?[-180,south,180,north]:[wrap(west),south,wrap(east),north];
  },
  viewportWidthKm(bounds){
    const [west,south,east,north]=bounds,radians=Math.PI/180,latitude=(south+north)*radians/2;
    const span=east>=west?east-west:east+360-west,segments=Math.max(1,Math.ceil(span/90));
    // Split wide and whole-world extents so the shortest endpoint arc cannot
    // mistake a wrapped national view for a narrow local view.
    const half=Math.sin(span*radians/(2*segments)),a=Math.min(1,Math.cos(latitude)**2*half**2);
    return segments*2*6371*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
  },
  selectChallenge(snapshot,bounds,national=false){
    const groups=new Map(),[west,south,east,north]=bounds;
    for(const row of snapshot.observations){
      const lon=row.longitude,lat=row.latitude;
      if(!(west<=east?west<=lon&&lon<=east:lon>=west||lon<=east)||lat<south||lat>north)continue;
      if(!groups.has(row.taxon_id))groups.set(row.taxon_id,[]);
      groups.get(row.taxon_id).push(row);
    }
    const time=r=>Date.parse(r.observed_at||r.observed_on+'T00:00:00+00:00');
    const order=(a,b)=>time(b)-time(a)||b.id-a.id;
    for(const group of groups.values())group.sort(order);
    const result=[],limit=national?500:150;
    for(let round=0;result.length<limit;round++){
      const candidates=[...groups.values()].filter(g=>g.length>round).map(g=>g[round]).sort(order);
      if(!candidates.length)break;
      result.push(...candidates.slice(0,limit-result.length));
    }
    return result;
  },
  availableCells(product,res){return product.tiers[String(res)].cells.filter(r=>r[4]!==null&&r[5]===r[6]&&r[3]>0);},
  async fetchPinned(descriptor,kind,fetcher=fetch,subtle=crypto.subtle,href=location.href){
    const url=new URL(descriptor.path,href);
    if(url.origin!==new URL(href).origin)throw Error('Cross-origin optional data');
    url.searchParams.set('v',descriptor.sha256);
    const response=await fetcher(url.href,{credentials:'same-origin',signal:AbortSignal.timeout(20000)});
    if(!response.ok)throw Error('Optional data HTTP '+response.status);
    const bytes=await response.arrayBuffer();
    const digest=[...new Uint8Array(await subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
    if(digest!==descriptor.sha256)throw Error('Optional data content hash mismatch');
    const product=JSON.parse(new TextDecoder().decode(bytes));
    if(product.schema_version!==1||product.schema_version!==descriptor.schema_version)throw Error('Unsupported optional data version');
    if(kind==='discovery'){
      if(product.metric!==descriptor.metric||product.scope.filters.taxon_group!==descriptor.taxon_group||
        JSON.stringify(product.scope)!==JSON.stringify(descriptor.scope))throw Error('Discovery scope mismatch');
    }else if(product.complete!==true||product.as_of!==descriptor.as_of||
      product.window.start!==descriptor.window.start||product.window.end!==descriptor.window.end)throw Error('Challenge snapshot mismatch');
    return product;
  },
  stale(asOf,now=Date.now()){return now-Date.parse(asOf)>36*60*60*1000;}
};

function installOptionalLayers(){
  const descriptors=OPTIONAL_LAYERS,cache={},pending={},indices={};
  let recentOn=false,recentError=false,discoveryError=false,selectedSighting=null,sightingBody=null,maxRate=0;
  const node=id=>document.getElementById(id);
  const fmt=value=>Number(value).toLocaleString(LANG==='fr'?'fr-CA':'en-CA',{maximumFractionDigits:2});
  const isDiscovery=()=>overlay==='discovery';
  const supports=group=>!!descriptors.discovery&&descriptors.discovery.taxon_group===group;
  const bounds=()=>{const b=map.getBounds();return OptionalData.viewport(b.getWest(),b.getSouth(),b.getEast(),b.getNorth());};
  async function load(kind){
    if(cache[kind])return cache[kind];
    if(pending[kind])return pending[kind];
    if(!descriptors[kind])throw Error('Optional data is not configured');
    pending[kind]=OptionalData.fetchPinned(descriptors[kind],kind).then(data=>{
      cache[kind]=data;
      if(kind==='discovery'){
        for(const res of [5000,25000])indices[res]=new Map(OptionalData.availableCells(data,res).map(r=>[r[0]+','+r[1],r]));
        for(const row of indices[5000].values())maxRate=Math.max(maxRate,row[4]);
      }
      return data;
    }).finally(()=>{delete pending[kind];});
    return pending[kind];
  }
  function discoveryCell(lat,lon,res){
    if(!isDiscovery()||!supports(state.taxon)||!indices[res])return null;
    const s=snapToLattice(lat,lon,res);return s?indices[res].get(s.row+','+s.col)||null:null;
  }
  const suppliedPoints=()=>descriptors.discovery?.scope.filters.discovery_points==='lab-supplied point set; original selection unknown';
  const discoveryUnit=()=>t(suppliedPoints()?'discovery_supplied_unit':'discovery_unit');
  const discoveryCounts=(n,exposure)=>t(suppliedPoints()?'discovery_supplied_counts':'discovery_counts',n,exposure);
  function sourceText(){
    const d=descriptors.discovery;if(!d)return '';
    const detail=d.scope.filters.discovery_points;
    const note=typeof detail==='string'&&detail.trim()?(suppliedPoints()?t('discovery_supplied_points'):t('discovery_point_scope')+': '+detail):'';
    return t('discovery_period',d.scope.period.start,d.scope.period.end)+(note?'<br>'+esc(note):'');
  }
  function discoveryPopup(lat,lon,res){
    const row=discoveryCell(lat,lon,res);
    return '<b>'+t('discovery_rate')+'</b><br>'+(row?discoveryCounts(fmt(row[2]),fmt(row[3]))+'<br><b>'+fmt(row[4])+'</b> '+discoveryUnit():t('discovery_unavailable'))+'<br>'+sourceText();
  }
  function ensureSource(id,type,paint){
    if(map.getSource(id))return;
    map.addSource(id,{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    map.addLayer({id,type,source:id,paint},id==='measured-discovery'&&map.getLayer('recent-challenge')?'recent-challenge':undefined);
    map.on('mouseenter',id,()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave',id,()=>{map.getCanvas().style.cursor='';});
    map.on('click',id,e=>{
      if(id==='measured-discovery'){if(!hitRecent(e.point))exploreCell(e.lngLat.lat,e.lngLat.lng);return;}
      const feature=e.features&&e.features[0],row=feature&&cache.challenge.observations.find(r=>r.id===feature.properties.id);
      if(row)openSighting(row);
    });
  }
  function visibleDiscovery(){
    const res=activeCellResM(),index=indices[res],result=[];
    if(!index)return result;
    const [w,s,e,n]=bounds(),points=[[w,s],[e,s],[w,n],[e,n],[(w+e)/2,s],[(w+e)/2,n],[w,(s+n)/2],[e,(s+n)/2]].map(p=>LAEA.fwd(p[1],p[0]));
    const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
    const c0=Math.max(0,Math.floor((Math.min(...xs)-LATTICE_X0)/res)-1),c1=Math.min(LATTICE_W/res-1,Math.floor((Math.max(...xs)-LATTICE_X0)/res)+1);
    const r0=Math.max(0,Math.floor((LATTICE_Y1-Math.max(...ys))/res)-1),r1=Math.min(LATTICE_H/res-1,Math.floor((LATTICE_Y1-Math.min(...ys))/res)+1);
    if((r1-r0+1)*(c1-c0+1)>45000)return result;
    for(let row=r0;row<=r1;row++)for(let col=c0;col<=c1;col++){const cell=index.get(row+','+col);if(cell)result.push(cell);}
    return result;
  }
  function renderButtons(list,rows,keyName,keyFor,paint){
    const focused=document.activeElement,existing=new Map([...list.children].map(el=>[el.dataset[keyName],el])),wanted=new Set();
    if(!list.children.length)list.textContent='';
    for(const [index,row]of rows.entries()){
      const key=keyFor(row),button=existing.get(key)||document.createElement('button');wanted.add(button);
      button.type='button';button.className='optional-cell';button.dataset[keyName]=key;paint(button,row);
      if(list.children[index]!==button)list.insertBefore(button,list.children[index]||null);
    }
    for(const button of [...list.children])if(!wanted.has(button))button.remove();
    if(wanted.has(focused)&&document.activeElement===document.body)focused.focus({preventScroll:true});
  }
  function renderDiscovery(){
    const panel=node('discoveryEvidence');if(panel)panel.hidden=!isDiscovery();
    if(!_mapLoaded)return;
    if(!isDiscovery()||!supports(state.taxon)||!cache.discovery){
      if(map.getLayer('measured-discovery'))map.setLayoutProperty('measured-discovery','visibility','none');
      return;
    }
    ensureSource('measured-discovery','fill',{'fill-color':['get','colour'],'fill-opacity':0.65,'fill-outline-color':'rgba(255,255,255,0.15)'});
    map.setLayoutProperty('measured-discovery','visibility','visible');
    const res=activeCellResM(),rows=visibleDiscovery();
    const features=rows.map(row=>{
      const x=LATTICE_X0+row[1]*res,y=LATTICE_Y1-row[0]*res;
      const ring=[[x,y],[x+res,y],[x+res,y-res],[x,y-res],[x,y]].map(p=>{const ll=LAEA.inv(...p);return[ll[1],ll[0]];});
      return{type:'Feature',properties:{colour:virColour(maxRate?row[4]/maxRate:0)},geometry:{type:'Polygon',coordinates:[ring]}};
    });
    map.getSource('measured-discovery').setData({type:'FeatureCollection',features});
    if(res===5000)DRAWN5=new Set(rows.map(r=>r[1]+','+r[0]));
    const list=node('discoveryCells');
    const ranked=rows.slice().sort((a,b)=>b[4]-a[4]||a[0]-b[0]||a[1]-b[1]).slice(0,30);
    renderButtons(list,ranked,'discoveryCell',row=>res+':'+row[0]+','+row[1],(button,row)=>{
      const ll=LAEA.inv(LATTICE_X0+(row[1]+0.5)*res,LATTICE_Y1-(row[0]+0.5)*res);
      button.textContent=ll[0].toFixed(2)+', '+ll[1].toFixed(2)+' · '+fmt(row[4])+' / 10 000';
      button.setAttribute('aria-label',button.textContent+' '+discoveryUnit()+'. '+discoveryCounts(fmt(row[2]),fmt(row[3])));
      button.onclick=()=>exploreCell(ll[0],ll[1]);
    });
    if(!rows.length)list.textContent=t('discovery_unavailable');
  }
  function discoveryLegend(){
    return '<div class="lt">'+t('discovery_rate')+'</div><div class="ramp"></div><div class="lab"><span>0</span><span>'+fmt(maxRate)+'</span></div><div class="hint">'+discoveryUnit()+'</div><div class="hint">'+sourceText()+'</div><div class="hint">'+t(discoveryError?'optional_error':'discovery_blurb')+'</div>';
  }
  function paintSighting(){
    if(!selectedSighting||!sightingBody)return;
    sightingBody.querySelector('b').textContent=selectedSighting.name;
    sightingBody.querySelector('p').textContent=t('sighting_date',selectedSighting.observed_on);
    sightingBody.querySelector('a').textContent=t('view_sighting');
  }
  function openSighting(row){
    closePopup();selectedSighting=row;sightingBody=document.createElement('div');sightingBody.className='popbody';
    const name=document.createElement('b'),date=document.createElement('p'),link=document.createElement('a');
    link.href='https://www.inaturalist.org/observations/'+row.id;link.target='_blank';link.rel='noopener';
    sightingBody.append(name,date,link);paintSighting();
    const popup=wirePopup(new maplibregl.Popup({focusAfterOpen:false,maxWidth:'320px'}).setLngLat([row.longitude,row.latitude]).setDOMContent(sightingBody));
    popup.on('close',()=>{if(selectedSighting===row){selectedSighting=null;sightingBody=null;}});popup.addTo(map);
  }
  function renderChallenge(){
    const panel=node('recentChallengePanel');panel.hidden=!descriptors.challenge;
    const status=node('recentChallengeStatus'),list=node('recentChallengeList');
    if(!recentOn){status.textContent=t('recent_challenge_hint');list.replaceChildren();}
    else if(!cache.challenge)status.textContent=t(recentError?'optional_error':'optional_loading');
    if(!_mapLoaded)return;
    if(!recentOn||!cache.challenge){if(map.getLayer('recent-challenge'))map.setLayoutProperty('recent-challenge','visibility','none');return;}
    ensureSource('recent-challenge','circle',{'circle-radius':6,'circle-color':'#d96c14','circle-stroke-width':2,'circle-stroke-color':'#fff'});
    map.setLayoutProperty('recent-challenge','visibility','visible');
    const snapshot=cache.challenge,view=bounds(),rows=OptionalData.selectChallenge(snapshot,view,OptionalData.viewportWidthKm(view)>500);
    const features=rows.map(row=>({type:'Feature',properties:{id:row.id},geometry:{type:'Point',coordinates:[row.longitude,row.latitude]}}));
    map.getSource('recent-challenge').setData({type:'FeatureCollection',features});
    status.textContent=t('recent_challenge_status',snapshot.window.start,snapshot.window.end,snapshot.as_of,rows.length)+(OptionalData.stale(snapshot.as_of)?' '+t('optional_stale'):'');
    renderButtons(list,rows,'sightingId',row=>String(row.id),(button,row)=>{
      button.textContent=row.name+' · '+row.observed_on;
      button.onclick=()=>{openSighting(row);map.jumpTo({center:[row.longitude,row.latitude],zoom:Math.max(7,map.getZoom())});};
    });
    if(!rows.length)list.textContent=t('recent_challenge_empty');
    paintSighting();
  }
  async function toggleRecent(enabled){
    recentOn=!!enabled&&!!descriptors.challenge;recentError=false;if(!recentOn&&selectedSighting)closePopup();node('recentChallenge').checked=recentOn;renderChallenge();syncSharedCellUrl(true);
    if(recentOn)try{await load('challenge');}catch(e){recentError=true;}
    renderChallenge();
  }
  async function prepareDiscovery(){
    discoveryError=false;
    try{await load('discovery');}catch(e){discoveryError=true;throw e;}
  }
  function refresh(){renderDiscovery();renderChallenge();}
  function appendCriteria(select){if(!descriptors.discovery)return;const option=document.createElement('option');option.value='discovery';option.textContent=t('discovery_rate');select.appendChild(option);}
  function hitRecent(point){return recentOn&&_mapLoaded&&!!map.getLayer('recent-challenge')&&map.queryRenderedFeatures(point,{layers:['recent-challenge']}).length>0;}
  globalThis.optionalLayers={isDiscovery,supports,load:prepareDiscovery,refresh,appendCriteria,discoveryCell,discoveryPopup,discoveryLegend,hitRecent,toggleRecent,
    recentEnabled:()=>recentOn,hasChallenge:()=>!!descriptors.challenge,group:()=>descriptors.discovery&&descriptors.discovery.taxon_group};
  node('recentChallengePanel').hidden=!descriptors.challenge;
  node('recentChallenge').addEventListener('change',e=>{_shareAction++;toggleRecent(e.target.checked);});
  map.on('moveend',refresh);whenMap(refresh);
}
