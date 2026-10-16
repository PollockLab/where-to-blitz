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
  matchingChallenge(snapshot,bounds){
    const [west,south,east,north]=bounds;
    return snapshot.observations.filter(row=>{
      const lon=row.longitude,lat=row.latitude;
      return (west<=east?west<=lon&&lon<=east:lon>=west||lon<=east)&&lat>=south&&lat<=north;
    });
  },
  selectChallenge(snapshot,bounds,national=false){
    const groups=new Map();
    for(const row of this.matchingChallenge(snapshot,bounds)){
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
  rateScale(rate,maximum){return maximum>0?Math.log1p(rate)/Math.log1p(maximum):0;},
  relativeRefresh(asOf,now=Date.now()){
    const day=value=>Math.floor(value/86400000),days=day(now)-day(Date.parse(asOf));
    return days<=0?['recent_updated_today']:days===1?['recent_updated_yesterday']:['recent_updated_days',days];
  },
  photoUrl(url){
    try{const u=new URL(url);return u.protocol==='https:'&&!u.port&&!u.username&&!u.password&&!u.search&&!u.hash&&['static.inaturalist.org','inaturalist-open-data.s3.amazonaws.com'].includes(u.hostname)&&/^\/photos\/[1-9]\d*\/(square|small|medium)\.(jpg|jpeg|png)$/i.test(u.pathname)?u.href:'';}catch{return '';}
  },
  // Mirrors iNaturalist app/models/shared/license_module.rb (CC 4.0, CC0 1.0).
  photoLicense(code){return code==='cc0'?'https://creativecommons.org/publicdomain/zero/1.0/':['cc-by','cc-by-sa','cc-by-nc','cc-by-nc-sa','cc-by-nd','cc-by-nc-nd'].includes(code)?'https://creativecommons.org/licenses/'+code.slice(3)+'/4.0/':'';},
  stale(asOf,now=Date.now()){return now-Date.parse(asOf)>36*60*60*1000;}
};

function installOptionalLayers(){
  const descriptors=OPTIONAL_LAYERS,cache={},pending={},indices={};
  let recentOn=false,recentError=false,discoveryError=false,selectedSighting=null,sightingBody=null;
  const rateLimits={};
  let discoveryLegendRes=null,discoveryPage=0,discoveryListView='',discoveryListRows=[],discoveryListRes=25000;
  let placeContext=null;
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
        for(const res of [5000,25000]){const rates=[...indices[res].values()].map(r=>r[4]).filter(r=>r>0);rateLimits[res]={maximum:rates.reduce((a,b)=>Math.max(a,b),0),minimum:rates.reduce((a,b)=>Math.min(a,b),Infinity)};}
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
  const sourceDetails=()=>'<details class="optional-source-details"><summary>'+t('source_details')+'</summary>'+sourceText()+'<p>'+discoveryUnit()+'</p><p>'+t('discovery_blurb')+'</p><p>'+t('discovery_log_scale')+' '+t('discovery_support')+'</p></details>';
  function discoveryPopup(lat,lon,res){
    const row=discoveryCell(lat,lon,res),context=cellContext(lat,lon,res);
    return (context?'<div class="sighting-place">'+esc(context)+'</div>':'')+'<b>'+t('discovery_rate')+'</b><br>'+(row?'<b>'+fmt(row[4])+'</b> '+discoveryUnit()+'<p>'+discoveryCounts(fmt(row[2]),fmt(row[3]))+'</p>'+(row[4]===0?'<p>'+t('discovery_zero_explanation')+'</p>':'')+'<small>'+t('discovery_support')+'</small>':t('discovery_unavailable'))+sourceDetails();
  }
  function cellContext(lat,lon,res){
    let nearest=null,distance=Infinity;
    for(const row of [...(cache.challenge?.observations||[]),...(placeContext?[placeContext]:[])]){
      if(!row.place_name)continue;
      const dx=(row.longitude-lon)*Math.cos(lat*Math.PI/180)*111.32,dy=(row.latitude-lat)*111.32,d=Math.hypot(dx,dy);
      if(d<distance){distance=d;nearest=row;}
    }
    return nearest&&distance<=Math.SQRT2*res/1000? t('discovery_near_place',nearest.place_name):null;
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
    if(isDiscovery()&&!cache.discovery)renderDiscoveryList();
    if(!_mapLoaded)return;
    if(!isDiscovery()||!supports(state.taxon)||!cache.discovery){
      if(map.getLayer('measured-discovery'))map.setLayoutProperty('measured-discovery','visibility','none');
      return;
    }
    ensureSource('measured-discovery','fill',{'fill-color':['get','colour'],'fill-opacity':['get','opacity'],'fill-outline-color':['get','outline']});
    map.setLayoutProperty('measured-discovery','visibility','visible');
    // Coarse support is bounded nationally. Projecting a wrapped viewport's
    // corners can miss most of Canada, so let the map clip this small tier.
    const res=activeCellResM(),rows=res===25000?[...indices[res].values()]:visibleDiscovery();
    if(discoveryLegendRes!==res){discoveryLegendRes=res;updateLegend();}
    const features=rows.map(row=>{
      const x=LATTICE_X0+row[1]*res,y=LATTICE_Y1-row[0]*res;
      const ring=[[x,y],[x+res,y],[x+res,y-res],[x,y-res],[x,y]].map(p=>{const ll=LAEA.inv(...p);return[ll[1],ll[0]];});
      return{type:'Feature',properties:{colour:row[4]===0?'#94a39d':virColour(OptionalData.rateScale(row[4],rateLimits[res].maximum)),opacity:row[4]===0?0.62:0.7,outline:row[4]===0?'#586b61':'rgba(255,255,255,0.15)',rate:row[4],discovery_count:row[2],observation_count:row[3]},geometry:{type:'Polygon',coordinates:[ring]}};
    });
    map.getSource('measured-discovery').setData({type:'FeatureCollection',features});
    if(res===5000)DRAWN5=new Set(rows.map(r=>r[1]+','+r[0]));
    renderDiscoveryList(rows,res);
  }
  function renderDiscoveryList(rows=discoveryListRows,res=discoveryListRes){
    discoveryListRows=rows;discoveryListRes=res;
    const list=node('discoveryCells'),select=node('discoveryCellFilter');
    select.disabled=!cache.discovery;
    if(!cache.discovery){
      discoveryPage=0;list.textContent='';
      node('discoveryCellStatus').textContent=t('discovery_loading');
      for(const id of ['discoveryPrevious','discoveryNext'])node(id).setAttribute('aria-disabled','true');
      return;
    }
    const filter=select.value||'all';
    const [west,south,east,north]=bounds(),view=[res,filter,west,south,east,north].join(',');
    if(view!==discoveryListView){discoveryPage=0;discoveryListView=view;}
    const inView=rows.filter(row=>{
      const [lat,lon]=LAEA.inv(LATTICE_X0+(row[1]+0.5)*res,LATTICE_Y1-(row[0]+0.5)*res);
      return lat>=south&&lat<=north&&(west<=east?lon>=west&&lon<=east:lon>=west||lon<=east);
    });
    const matching=inView.filter(row=>filter==='zero'?row[4]===0:filter==='positive'?row[4]>0:true).sort((a,b)=>b[4]-a[4]||a[0]-b[0]||a[1]-b[1]);
    discoveryPage=Math.min(discoveryPage,Math.max(0,Math.ceil(matching.length/30)-1));
    const start=discoveryPage*30,ranked=matching.slice(start,start+30);
    node('discoveryCellStatus').textContent=t('discovery_list_scope',fmt(ranked.length?start+1:0),fmt(start+ranked.length),fmt(matching.length),fmt(inView.length));
    node('discoveryPrevious').setAttribute('aria-disabled',String(discoveryPage===0));
    node('discoveryNext').setAttribute('aria-disabled',String(start+30>=matching.length));
    const focused=document.activeElement;
    renderButtons(list,ranked,'discoveryCell',row=>res+':'+row[0]+','+row[1],(button,row)=>{
      const ll=LAEA.inv(LATTICE_X0+(row[1]+0.5)*res,LATTICE_Y1-(row[0]+0.5)*res);
      const coordinates=ll[0].toFixed(2)+', '+ll[1].toFixed(2),context=cellContext(ll[0],ll[1],res);
      button.textContent=(context||coordinates)+' · '+fmt(row[4])+' / 10 000\n'+discoveryCounts(fmt(row[2]),fmt(row[3]))+(context?'\n'+coordinates:'');
      button.setAttribute('aria-label',button.textContent+' '+discoveryUnit()+'. '+discoveryCounts(fmt(row[2]),fmt(row[3])));
      button.onclick=()=>exploreCell(ll[0],ll[1]);
    });
    if(!ranked.length)list.textContent=t(inView.length?'discovery_list_empty':'discovery_unavailable');
    if(focused?.dataset.discoveryCell&&!focused.isConnected&&document.activeElement===document.body)node('discoveryCellFilter').focus({preventScroll:true});
  }
  function discoveryLegend(){
    const limits=rateLimits[activeCellResM()]||{minimum:0,maximum:0};
    const low=Number.isFinite(limits.minimum)?limits.minimum:0;
    const start=OptionalData.rateScale(low,limits.maximum),gradient=Array.from({length:8},(_,i)=>virColour(start+(1-start)*i/7)+' '+(100*i/7)+'%').join(',');
    const ramp=limits.maximum>0?'<div class="ramp optional-positive-ramp" style="background:linear-gradient(to right,'+gradient+')"></div><div class="lab"><span>'+fmt(low)+'</span><span>'+fmt(limits.maximum)+'</span></div><div class="hint">'+t('discovery_log_key')+'</div>':'';
    return '<div class="lt">'+t('discovery_rate')+'</div>'+ramp+'<div class="hint">'+t('discovery_legend_unit')+'</div><div class="optional-legend-key"><span><span class="optional-swatch optional-zero"></span>'+t('discovery_zero')+'</span><span><span class="optional-swatch optional-missing"></span>'+t('discovery_no_data')+'</span></div><div class="hint">'+t('discovery_sparse')+'</div>'+(recentOn?'<div class="hint">'+t('discovery_recent_separate')+'</div>':'')+sourceDetails()+(discoveryError?'<div class="hint">'+t('optional_error')+'</div>':'');
  }
  function paintSighting(){
    if(!selectedSighting||!sightingBody)return;
    const row=selectedSighting;if(sightingBody.paintLanguage===LANG)return;
    const focusTargets={};
    const focusedAction=sightingBody.contains(document.activeElement)?document.activeElement.dataset?.sightingAction:null;
    sightingBody.replaceChildren();sightingBody.paintLanguage=LANG;
    const name=document.createElement('b'),scientific=document.createElement('p'),date=document.createElement('p'),place=document.createElement('p'),links=document.createElement('div'),view=document.createElement('a'),record=document.createElement('a');
    name.textContent=row.common_name||row.identified_name||row.name;
    scientific.textContent=row.common_name?(row.identified_name||row.name):'';
    date.textContent=t('sighting_date',row.observed_on);place.textContent=row.place_name||'';place.className='sighting-place';
    sightingBody.append(name);if(scientific.textContent)sightingBody.append(scientific);
    const photo=row.photo,url=photo&&photo.attribution&&OptionalData.photoLicense(photo.license_code)&&OptionalData.photoUrl(photo.url);
    if(url){
      const image=document.createElement('img'),credit=document.createElement('small'),source=document.createElement('a'),license=document.createElement('a');image.src=url;image.alt=photo.alt||row.identified_name||row.name;image.loading='lazy';image.className='sighting-photo';credit.className='sighting-credit';credit.textContent=t('photo_reference')+'. '+t('photo_credit')+': ';
      source.href='https://www.inaturalist.org/photos/'+new URL(url).pathname.split('/')[2];source.textContent=photo.attribution;license.href=OptionalData.photoLicense(photo.license_code);license.textContent=photo.license_code.toUpperCase();
      for(const [key,link] of [['photo',source],['license',license]]){link.target='_blank';link.rel='noopener';link.dataset.sightingAction=key;focusTargets[key]=link;}const separator=document.createElement('span');separator.textContent=' · ';credit.append(source,separator,license);sightingBody.append(image,credit);
    }
    sightingBody.append(date);if(place.textContent)sightingBody.append(place);
    view.href='https://www.inaturalist.org/observations/'+row.id;view.textContent=t('view_sighting');record.href='https://www.inaturalist.org/observations/new';record.textContent=t('record_sighting');
    for(const [index,link] of [view,record].entries()){link.target='_blank';link.rel='noopener';link.dataset.sightingAction=String(index);focusTargets[String(index)]=link;}links.className='sighting-actions';links.append(view,record);sightingBody.append(links);
    if(focusedAction!==null&&focusedAction!==undefined)focusTargets[focusedAction]?.focus({preventScroll:true});
  }
  function openSighting(row){
    closePopup();selectedSighting=row;sightingBody=document.createElement('div');sightingBody.className='popbody';paintSighting();
    const popup=wirePopup(new maplibregl.Popup({focusAfterOpen:false,maxWidth:'320px'}).setLngLat([row.longitude,row.latitude]).setDOMContent(sightingBody));
    popup.on('close',()=>{if(selectedSighting===row){selectedSighting=null;sightingBody=null;}});popup.addTo(map);
  }
  let coverageSnapshot=null,coverageLanguage=null;
  function renderCoverage(){
    const snapshot=cache.challenge,coverage=snapshot?.coverage,container=node('recentChallengeCoverage');
    container.hidden=!coverage;
    if(!coverage)return;
    node('recentChallengeCoverageCounts').textContent=t('recent_challenge_coverage',fmt(coverage.unique_targets),fmt(coverage.resolved_rows),fmt(coverage.source_rows));
    const details=node('recentChallengeOmissions');details.hidden=!coverage.omitted_rows;
    node('recentChallengeOmissionsSummary').textContent=t('recent_challenge_omissions',fmt(coverage.omitted_rows));
    if(coverageSnapshot===snapshot&&coverageLanguage===LANG)return;
    const reasons=new Set(['blank_scientific_name','inactive_source_taxon','no_unique_exact_match','incomplete_search','broader_than_species','rank_mismatch','missing_species_ancestry']);
    const list=node('recentChallengeOmissionList');list.replaceChildren();
    for(const entry of coverage.omissions){
      const item=document.createElement('li'),name=document.createElement('b'),common=document.createElement('p'),reason=document.createElement('p');
      name.textContent=entry.raw_scientific_name||t('recent_challenge_unnamed');
      common.textContent=entry.common_name?t('recent_challenge_common')+': '+entry.common_name:'';
      reason.textContent=reasons.has(entry.reason_code)?t('challenge_omit_'+entry.reason_code):entry.reason;
      item.append(name);if(entry.common_name)item.append(common);item.append(reason);list.append(item);
    }
    coverageSnapshot=snapshot;coverageLanguage=LANG;
  }
  function renderChallenge(){
    const panel=node('recentChallengePanel');panel.hidden=!descriptors.challenge;
    const status=node('recentChallengeStatus'),list=node('recentChallengeList');renderCoverage();
    if(!recentOn){status.textContent=t('recent_challenge_hint');list.replaceChildren();}
    else if(!cache.challenge)status.textContent=t(recentError?'optional_error':'optional_loading');
    if(!_mapLoaded)return;
    if(!recentOn||!cache.challenge){if(map.getLayer('recent-challenge'))map.setLayoutProperty('recent-challenge','visibility','none');return;}
    ensureSource('recent-challenge','circle',{'circle-radius':6,'circle-color':'#d96c14','circle-stroke-width':2,'circle-stroke-color':'#fff'});
    map.setLayoutProperty('recent-challenge','visibility','visible');
    const snapshot=cache.challenge,view=bounds(),national=OptionalData.viewportWidthKm(view)>500,rows=OptionalData.selectChallenge(snapshot,view,national),matching=OptionalData.matchingChallenge(snapshot,view).length;
    const features=rows.map(row=>({type:'Feature',properties:{id:row.id},geometry:{type:'Point',coordinates:[row.longitude,row.latitude]}}));
    map.getSource('recent-challenge').setData({type:'FeatureCollection',features});
    status.textContent=t('recent_challenge_all_groups')+' · '+t('recent_challenge_shown',fmt(rows.length),fmt(matching),national?500:150)+' · '+t(...OptionalData.relativeRefresh(snapshot.as_of))+(OptionalData.stale(snapshot.as_of)?' '+t('optional_stale'):'');
    status.title=t('recent_exact_refresh')+': '+snapshot.as_of+' UTC. '+snapshot.window.start+' – '+snapshot.window.end;
    const metadata=node('recentChallengeMeta');if(metadata)metadata.textContent=status.title;
    const freshness=node('recentChallengeFreshness');if(freshness){freshness.textContent=t(...OptionalData.relativeRefresh(snapshot.as_of));freshness.title=status.title;freshness.dateTime=snapshot.as_of;}
    renderButtons(list,rows,'sightingId',row=>String(row.id),(button,row)=>{
      button.textContent=(row.common_name||row.identified_name||row.name)+' · '+row.observed_on+(row.place_name?'\n'+row.place_name:'');
      button.onclick=()=>{openSighting(row);map.jumpTo({center:[row.longitude,row.latitude],zoom:Math.max(7,map.getZoom())});};
    });
    if(!rows.length)list.textContent=t('recent_challenge_empty');
    paintSighting();
  }
  async function toggleRecent(enabled){
    recentOn=!!enabled&&!!descriptors.challenge;recentError=false;if(!recentOn&&selectedSighting)closePopup();node('recentChallenge').checked=recentOn;renderChallenge();syncSharedCellUrl(true);if(isDiscovery())updateLegend();
    if(recentOn)try{await load('challenge');}catch(e){recentError=true;}
    renderChallenge();renderDiscovery();
  }
  async function prepareDiscovery(){
    discoveryError=false;
    try{await load('discovery');}catch(e){discoveryError=true;throw e;}
  }
  function refresh(){renderDiscovery();renderChallenge();}
  function appendCriteria(select){if(!descriptors.discovery)return;const option=document.createElement('option');option.value='discovery';option.textContent=t('discovery_rate');select.appendChild(option);}
  function hitRecent(point){return recentOn&&_mapLoaded&&!!map.getLayer('recent-challenge')&&map.queryRenderedFeatures(point,{layers:['recent-challenge']}).length>0;}
  globalThis.optionalLayers={setPlaceContext(context){placeContext=context&&Number.isFinite(context.lat)&&Number.isFinite(context.lon)&&typeof context.name==='string'?{latitude:context.lat,longitude:context.lon,place_name:context.name}:null;renderDiscovery();},isDiscovery,supports,load:prepareDiscovery,refresh,appendCriteria,discoveryCell,discoveryPopup,discoveryLegend,hitRecent,toggleRecent,
    recentEnabled:()=>recentOn,hasChallenge:()=>!!descriptors.challenge,group:()=>descriptors.discovery&&descriptors.discovery.taxon_group};
  node('recentChallengePanel').hidden=!descriptors.challenge;
  node('recentChallenge').addEventListener('change',e=>{_shareAction++;toggleRecent(e.target.checked);});
  node('discoveryCellFilter').onchange=()=>{discoveryPage=0;renderDiscoveryList();node('discoveryCells').scrollTop=0;};
  for(const [id,delta]of [['discoveryPrevious',-1],['discoveryNext',1]])node(id).onclick=()=>{
    if(!cache.discovery||node(id).getAttribute('aria-disabled')==='true')return;
    discoveryPage+=delta;renderDiscoveryList();node('discoveryCells').scrollTop=0;
  };
  map.on('moveend',refresh);whenMap(refresh);
}
