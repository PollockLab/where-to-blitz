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
  historicalGroups(points){
    const groups=new Map();
    for(const point of points){const key=point.longitude+','+point.latitude;if(!groups.has(key))groups.set(key,{key,longitude:point.longitude,latitude:point.latitude,points:[]});groups.get(key).points.push(point);}
    return [...groups.values()];
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
  const catalog=descriptors.challenge?.catalog||null;
  const catalogTaxa=catalog?[...catalog.taxa].sort((a,b)=>a.name.localeCompare(b.name,'en')||a.id-b.id):[];
  let catalogPage=0,catalogQuery='',catalogRendered='';
  let sightingPopup=null,sightingPosition=null,selectedSightings=[],recentPositions=new Map();
  const recentImages=new Set();
  const rateLimits={};
  let discoveryLegendRes=null,discoveryPage=0,discoveryListView='',discoveryListRows=[],discoveryListRes=25000;
  const historical=typeof HISTORICAL_DISCOVERY_POINTS==='undefined'?null:HISTORICAL_DISCOVERY_POINTS;
  const historicalGroups=historical?OptionalData.historicalGroups(historical.points):[];
  let historicalOn=true,historicalPage=0,historicalView='',historicalPopup=null,historicalSelected=null,historicalBody=null,historicalLanguage=null;
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
  function ensureSource(id,type,paint,layout={}){
    if(map.getSource(id))return;
    map.addSource(id,{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    map.addLayer({id,type,source:id,paint,layout},id==='measured-discovery'&&map.getLayer('recent-challenge')?'recent-challenge':undefined);
    map.on('mouseenter',id,()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave',id,()=>{map.getCanvas().style.cursor='';});
    map.on('click',id,e=>{
      if(id==='measured-discovery'){if(!hitRecent(e.point)&&!hitHistorical(e.point))exploreCell(e.lngLat.lat,e.lngLat.lng);return;}
      const group=recentPositions.get(e.features?.[0]?.properties.key);
      if(group)openSighting(group.points[0],group.points.length>1?group:null);
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
    const row=selectedSighting,selection=selectedSightings.map(r=>r.id).join(',')+':'+row.id;
    if(sightingBody.paintLanguage===LANG&&sightingBody.paintSelection===selection)return;
    const focusTargets={};
    const focusedAction=sightingBody.contains(document.activeElement)?document.activeElement.dataset?.sightingAction:null;
    sightingBody.replaceChildren();sightingBody.paintLanguage=LANG;sightingBody.paintSelection=selection;
    if(selectedSightings.length>1){
      const index=selectedSightings.indexOf(row),status=document.createElement('p'),navigation=document.createElement('div');
      status.className='optional-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');status.textContent=t('sighting_position_progress',fmt(index+1),fmt(selectedSightings.length));
      navigation.className='optional-pagination';
      for(const [key,delta]of [['previous',-1],['next',1]]){
        const button=document.createElement('button'),destination=index+delta;button.type='button';button.dataset.sightingAction=key;button.textContent=t('sighting_'+key);button.setAttribute('aria-disabled',String(destination<0||destination>=selectedSightings.length));
        button.onclick=()=>{if(destination<0||destination>=selectedSightings.length)return;selectedSighting=selectedSightings[destination];paintSighting();};
        focusTargets[key]=button;navigation.append(button);
      }
      sightingBody.append(status,navigation);
    }
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
    if(focusedAction!==null&&focusedAction!==undefined)(focusTargets[focusedAction]||view).focus({preventScroll:true});
  }
  function openSighting(row,group=null){
    closePopup();selectedSighting=row;selectedSightings=group?group.points:[row];sightingPosition=group?.key||null;sightingBody=document.createElement('div');sightingBody.className='popbody';paintSighting();
    const popup=wirePopup(new maplibregl.Popup({focusAfterOpen:false,maxWidth:'320px'}).setLngLat([row.longitude,row.latitude]).setDOMContent(sightingBody));
    sightingPopup=popup;popup.on('close',()=>{if(sightingPopup===popup){sightingPopup=null;selectedSighting=null;sightingBody=null;selectedSightings=[];sightingPosition=null;}});popup.addTo(map);
  }
  function ensureRecentImages(groups){
    for(const count of new Set(groups.map(group=>group.points.length))){
      if(recentImages.has(count))continue;
      const canvas=document.createElement('canvas'),size=count===1?32:64;canvas.width=size;canvas.height=size;const ctx=canvas.getContext('2d');
      ctx.beginPath();ctx.arc(size/2,size/2,count===1?12:28,0,Math.PI*2);ctx.fillStyle='#d96c14';ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=count===1?4:3;ctx.stroke();
      if(count>1){ctx.fillStyle='#111827';ctx.font='bold 25px sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(String(count),32,33);}
      map.addImage('recent-count-'+count,ctx.getImageData(0,0,size,size));recentImages.add(count);
    }
  }
  let coverageSnapshot=null,coverageLanguage=null;
  function renderCoverage(){
    const snapshot=cache.challenge,coverage=catalog?.coverage||snapshot?.coverage,container=node('recentChallengeCoverage');
    container.hidden=!catalog&&!coverage&&!descriptors.challenge?.source_version;
    node('challengeCatalogContext').hidden=!catalog;node('challengeCatalogRationale').hidden=!catalog;
    node('challengeCatalogBrowser').hidden=!catalog;node('challengeCatalogUnavailable').hidden=!!catalog;
    node('challengeCatalogSearch').disabled=!catalog;
    renderCatalog();
    node('recentChallengeCoverageCounts').hidden=!coverage;
    if(!coverage){node('recentChallengeOmissions').hidden=true;return;}
    node('recentChallengeCoverageCounts').textContent=t('recent_challenge_coverage',fmt(coverage.unique_targets),fmt(coverage.resolved_rows),fmt(coverage.source_rows));
    const details=node('recentChallengeOmissions');details.hidden=!coverage.omitted_rows;
    node('recentChallengeOmissionsSummary').textContent=t('recent_challenge_omissions',fmt(coverage.omitted_rows));
    if(coverageSnapshot===coverage&&coverageLanguage===LANG)return;
    const reasons=new Set(['blank_scientific_name','inactive_source_taxon','no_unique_exact_match','incomplete_search','broader_than_species','rank_mismatch','missing_species_ancestry']);
    const list=node('recentChallengeOmissionList');list.replaceChildren();
    for(const entry of coverage.omissions){
      const item=document.createElement('li'),name=document.createElement('b'),common=document.createElement('p'),reason=document.createElement('p');
      name.textContent=entry.raw_scientific_name||t('recent_challenge_unnamed');
      common.textContent=entry.common_name?t('recent_challenge_common')+': '+entry.common_name:'';
      reason.textContent=reasons.has(entry.reason_code)?t('challenge_omit_'+entry.reason_code):entry.reason;
      item.append(name);if(entry.common_name)item.append(common);item.append(reason);list.append(item);
    }
    coverageSnapshot=coverage;coverageLanguage=LANG;
  }
  function renderCatalog(){
    if(!catalog)return;
    const input=node('challengeCatalogSearch'),query=(input.value||'').trim().toLowerCase();
    if(query!==catalogQuery){catalogPage=0;catalogQuery=query;}
    const matching=catalogTaxa.filter(row=>row.name.toLowerCase().includes(query));
    catalogPage=Math.min(catalogPage,Math.max(0,Math.ceil(matching.length/30)-1));
    const key=[query,catalogPage,LANG].join('|');if(key===catalogRendered)return;catalogRendered=key;
    const start=catalogPage*30,rows=matching.slice(start,start+30),list=node('challengeCatalogList');
    node('challengeCatalogStatus').textContent=t('challenge_catalog_scope',rows.length?start+1:0,start+rows.length,matching.length,catalogTaxa.length);
    node('challengeCatalogPrevious').setAttribute('aria-disabled',String(catalogPage===0));node('challengeCatalogNext').setAttribute('aria-disabled',String(start+30>=matching.length));
    const focused=document.activeElement,existing=new Map([...list.children].map(el=>[el.dataset.catalogTarget,el])),wanted=new Set();
    if(!list.children.length)list.textContent='';
    for(const [index,row]of rows.entries()){
      const id=String(row.id),item=existing.get(id)||document.createElement('li');item.dataset.catalogTarget=id;item.dataset.rank=row.rank;wanted.add(item);
      let link=item.children[0],rank=item.children[1];if(!link){link=document.createElement('a');rank=document.createElement('span');item.append(link,rank);}
      link.textContent=row.name;link.href='https://www.inaturalist.org/taxa/'+row.id;link.target='_blank';link.rel='noopener';rank.textContent=' ('+t('challenge_catalog_rank')[row.rank]+')';
      if(list.children[index]!==item)list.insertBefore(item,list.children[index]||null);
    }
    for(const item of [...list.children])if(!wanted.has(item))item.remove();
    if(!rows.length)list.textContent=t('challenge_catalog_empty');
    if(focused!==document.body&&document.activeElement===document.body&&focused&&!focused.isConnected)input.focus({preventScroll:true});
  }
  function renderChallenge(){
    const panel=node('recentChallengePanel');panel.hidden=!descriptors.challenge;
    const status=node('recentChallengeStatus'),list=node('recentChallengeList');renderCoverage();
    const ready=recentOn&&!!cache.challenge&&_mapLoaded;
    for(const id of ['recentChallengeDetails','recentChallengeSightings'])node(id).hidden=!ready;
    if(!recentOn){status.textContent=t('recent_challenge_hint');list.replaceChildren();}
    else if(!cache.challenge)status.textContent=t(recentError?'optional_error':'optional_loading');
    if(!_mapLoaded)return;
    if(!recentOn||!cache.challenge){if(map.getLayer('recent-challenge'))map.setLayoutProperty('recent-challenge','visibility','none');return;}
    const snapshot=cache.challenge,view=bounds(),national=OptionalData.viewportWidthKm(view)>500,rows=OptionalData.selectChallenge(snapshot,view,national),matching=OptionalData.matchingChallenge(snapshot,view).length;
    // Reuse exact-coordinate grouping only after viewport filtering and the observation cap.
    const groups=OptionalData.historicalGroups(rows);recentPositions=new Map(groups.map(group=>[group.key,group]));ensureRecentImages(groups);
    ensureSource('recent-challenge','symbol',{}, {'icon-image':['concat','recent-count-',['to-string',['get','count']]],'icon-size':0.5,'icon-allow-overlap':true,'icon-ignore-placement':true});
    map.setLayoutProperty('recent-challenge','visibility','visible');
    const features=groups.map(group=>({type:'Feature',properties:{id:group.points[0].id,key:group.key,count:group.points.length},geometry:{type:'Point',coordinates:[group.longitude,group.latitude]}}));
    map.getSource('recent-challenge').setData({type:'FeatureCollection',features});
    status.textContent=t('recent_challenge_all_groups')+' · '+t('recent_challenge_shown',fmt(rows.length),fmt(matching),national?500:150)+' · '+t('recent_challenge_positions',fmt(groups.length))+' · '+t(...OptionalData.relativeRefresh(snapshot.as_of))+(OptionalData.stale(snapshot.as_of)?' '+t('optional_stale'):'');
    status.title=t('recent_exact_refresh')+': '+snapshot.as_of+' UTC. '+snapshot.window.start+' – '+snapshot.window.end;
    const metadata=node('recentChallengeMeta');if(metadata)metadata.textContent=status.title;
    const freshness=node('recentChallengeFreshness');if(freshness){freshness.textContent=t(...OptionalData.relativeRefresh(snapshot.as_of));freshness.title=status.title;freshness.dateTime=snapshot.as_of;}
    renderButtons(list,rows,'sightingId',row=>String(row.id),(button,row)=>{
      button.textContent=(row.common_name||row.identified_name||row.name)+' · '+row.observed_on+(row.place_name?'\n'+row.place_name:'');
      button.onclick=()=>{openSighting(row);map.jumpTo({center:[row.longitude,row.latitude],zoom:Math.max(7,map.getZoom())});};
    });
    if(!rows.length)list.textContent=t('recent_challenge_empty');
    if(sightingPosition){
      const group=recentPositions.get(sightingPosition);
      if(!group)sightingPopup.remove();
      else{selectedSightings=group.points;selectedSighting=selectedSightings.find(row=>row.id===selectedSighting.id)||selectedSightings[0];}
    }
    paintSighting();
  }
  async function toggleRecent(enabled){
    recentOn=!!enabled&&!!descriptors.challenge;recentError=false;if(!recentOn&&selectedSighting)closePopup();node('recentChallenge').checked=recentOn;renderChallenge();syncSharedCellUrl(true);if(isDiscovery())updateLegend();
    if(recentOn)try{await load('challenge');}catch(e){recentError=true;}
    renderChallenge();renderDiscovery();renderHistorical();
  }
  function historicalVisible(){return !!historical&&historicalOn&&isDiscovery()&&supports(state.taxon)&&activeCellResM()===5000&&!!cache.discovery;}
  function hitHistorical(point){return historicalVisible()&&_mapLoaded&&!!map.getLayer('historical-discovery')&&map.queryRenderedFeatures(point,{layers:['historical-discovery']}).length>0;}
  function paintHistoricalPopup(){
    if(!historicalSelected||!historicalBody||historicalLanguage===LANG)return;
    historicalLanguage=LANG;
    const group=historicalSelected;historicalBody.replaceChildren();
    const title=document.createElement('b'),list=document.createElement('ul'),detail=document.createElement('div');
    title.textContent=t('historical_location_count',group.points.length);
    for(const point of group.points){const item=document.createElement('li');item.textContent=point.scientific_name+' · '+point.observed_year;list.append(item);}
    const note=document.createElement('p');note.textContent=t('historical_supplied_names');
    detail.innerHTML=discoveryPopup(group.latitude,group.longitude,5000);historicalBody.append(title,note,list,detail);
  }
  function openHistorical(group){
    closePopup();historicalLanguage=null;historicalSelected=group;historicalBody=document.createElement('div');historicalBody.className='popbody';paintHistoricalPopup();
    const popup=wirePopup(new maplibregl.Popup({focusAfterOpen:false,maxWidth:'340px'}).setLngLat([group.longitude,group.latitude]).setDOMContent(historicalBody));historicalPopup=popup;
    popup.on('close',()=>{if(historicalPopup===popup){historicalPopup=null;historicalSelected=null;historicalBody=null;}});popup.addTo(map);
  }
  function renderHistorical(){
    node('historicalPointsPanel').hidden=!historical||!isDiscovery();
    node('historicalPointsToggle').checked=historicalOn;
    const visible=historicalVisible()&&_mapLoaded;
    node('historicalPointsDetails').hidden=!visible;
    node('historicalPointsHint').textContent=t(!historicalOn?'historical_hidden':activeCellResM()!==5000?'historical_zoom':!cache.discovery?'discovery_loading':'historical_local');
    if(!visible){
      const returnFocus=historicalPopup?.getElement()?.contains(document.activeElement)||!!document.activeElement?.dataset.historicalPoint;
      if(historicalPopup)historicalPopup.remove();
      if(_mapLoaded&&map.getLayer('historical-discovery'))map.setLayoutProperty('historical-discovery','visibility','none');
      node('historicalPointsList').replaceChildren();node('historicalPointsStatus').textContent='';
      for(const id of ['historicalPointsPrevious','historicalPointsNext'])node(id).setAttribute('aria-disabled','true');
      if(returnFocus&&!focusPopupTarget(node('historicalPointsToggle')))focusPopupTarget(map.getCanvas());
      return;
    }
    if(!_mapLoaded)return;
    if(!map.getSource('historical-discovery')){
      for(const count of new Set(historicalGroups.map(g=>g.points.length))){
        const canvas=document.createElement('canvas');canvas.width=64;canvas.height=64;const ctx=canvas.getContext('2d');
        ctx.beginPath();ctx.moveTo(32,2);ctx.lineTo(62,32);ctx.lineTo(32,62);ctx.lineTo(2,32);ctx.closePath();ctx.fillStyle='#006d77';ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=3;ctx.stroke();ctx.fillStyle='#fff';ctx.font='bold 25px sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(String(count),32,33);
        map.addImage('historical-count-'+count,ctx.getImageData(0,0,64,64));
      }
      map.addSource('historical-discovery',{type:'geojson',data:{type:'FeatureCollection',features:historicalGroups.map(g=>({type:'Feature',properties:{key:g.key,count:g.points.length},geometry:{type:'Point',coordinates:[g.longitude,g.latitude]}}))}});
      map.addLayer({id:'historical-discovery',type:'symbol',source:'historical-discovery',layout:{'icon-image':['concat','historical-count-',['to-string',['get','count']]],'icon-size':0.55,'icon-allow-overlap':true}},map.getLayer('recent-challenge')?'recent-challenge':undefined);
      map.on('click','historical-discovery',e=>{if(hitRecent(e.point))return;const key=e.features?.[0]?.properties.key,group=historicalGroups.find(g=>g.key===key);if(group)openHistorical(group);});
      map.on('mouseenter','historical-discovery',()=>{map.getCanvas().style.cursor='pointer';});map.on('mouseleave','historical-discovery',()=>{map.getCanvas().style.cursor='';});
    }
    map.setLayoutProperty('historical-discovery','visibility','visible');
    const view=bounds(),key=view.join(',');if(key!==historicalView){historicalPage=0;historicalView=key;}
    const [west,south,east,north]=view,groups=historicalGroups.filter(g=>g.latitude>=south&&g.latitude<=north&&(west<=east?g.longitude>=west&&g.longitude<=east:g.longitude>=west||g.longitude<=east));
    historicalPage=Math.min(historicalPage,Math.max(0,Math.ceil(groups.length/30)-1));const start=historicalPage*30,rows=groups.slice(start,start+30);
    node('historicalPointsStatus').textContent=t('historical_points_scope',rows.length?start+1:0,start+rows.length,groups.length);
    node('historicalPointsPrevious').setAttribute('aria-disabled',String(!historicalPage));node('historicalPointsNext').setAttribute('aria-disabled',String(start+30>=groups.length));
    const list=node('historicalPointsList'),focused=document.activeElement;
    renderButtons(list,rows,'historicalPoint',g=>g.key,(button,g)=>{button.textContent=g.points[0].scientific_name+' · '+g.points[0].observed_year+'\n'+t('historical_location_count',g.points.length);button.onclick=()=>openHistorical(g);});
    if(!rows.length)list.textContent=t('historical_empty');
    if(focused?.dataset.historicalPoint&&!focused.isConnected&&document.activeElement===document.body)node('historicalPointsToggle').focus({preventScroll:true});
    if(historicalSelected)paintHistoricalPopup();
  }
  async function prepareDiscovery(){
    discoveryError=false;
    try{await load('discovery');}catch(e){discoveryError=true;throw e;}
  }
  function refresh(){renderDiscovery();renderChallenge();renderHistorical();}
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
  node('historicalPointsToggle').onchange=e=>{historicalOn=e.target.checked;renderHistorical();};
  for(const [id,delta]of [['historicalPointsPrevious',-1],['historicalPointsNext',1]])node(id).onclick=()=>{if(!historicalVisible()||node(id).getAttribute('aria-disabled')==='true')return;historicalPage+=delta;renderHistorical();node('historicalPointsList').scrollTop=0;};
  node('challengeCatalogSearch').oninput=()=>{catalogPage=0;renderCatalog();node('challengeCatalogList').scrollTop=0;};
  for(const [id,delta]of [['challengeCatalogPrevious',-1],['challengeCatalogNext',1]])node(id).onclick=()=>{if(!catalog||node(id).getAttribute('aria-disabled')==='true')return;catalogPage+=delta;renderCatalog();node('challengeCatalogList').scrollTop=0;};
  map.on('moveend',refresh);whenMap(refresh);
}
