// Optional evidence for the published Getting Even categories. No estimator runs here.
const GettingEvenEvidence={
  cutoffs:[1,5,10,20,30,100],
  cutoff(value){return this.cutoffs.includes(+value)&&String(+value)===String(value)?+value:1;},
  count(image,col,row){
    if(!image||!Number.isInteger(col)||!Number.isInteger(row)||col<0||row<0||col>=image.w||row>=image.h)return null;
    const k=(row*image.w+col)*4;
    return image.d[k+3]===255?image.d[k]*65536+image.d[k+1]*256+image.d[k+2]:null;
  },
  create({descriptor,lattice,decode,fetcher,subtle,baseUrl,active,onChange}){
    const cache={},pending={},errors={};let cutoff=1;
    const tier=res=>descriptor?.schema_version===1&&descriptor.kind==='gettingeven-source-counts'&&descriptor.encoding==='rgb24'?descriptor.tiers?.[String(res)]:null;
    const ready=res=>!!cache[res];
    const state=res=>ready(res)?'ready':pending[res]?'loading':errors[res]?'error':tier(res)?'idle':'unavailable';
    async function pinned(path,sha){
      if(typeof sha!=='string'||! /^[0-9a-f]{64}$/.test(sha))throw Error('Invalid evidence checksum');
      const url=new URL(path,baseUrl);
      if(url.origin!==new URL(baseUrl).origin||url.search||url.hash)throw Error('Invalid evidence location');
      url.searchParams.set('v',sha);
      const response=await fetcher(url.href,{credentials:'same-origin',signal:AbortSignal.timeout(20000)});
      if(!response.ok)throw Error('Evidence HTTP '+response.status);
      const bytes=await response.arrayBuffer();
      const digest=[...new Uint8Array(await subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
      if(digest!==sha)throw Error('Evidence checksum mismatch');
      return decode(bytes);
    }
    function aligned(image,res,record){
      const expected=lattice(res),actual=record.lattice;
      if(!actual||['x0','y1','res_m','ncol','nrow'].some(key=>actual[key]!==expected[key])||image.w!==expected.ncol||image.h!==expected.nrow||image.d.length!==image.w*image.h*4)throw Error('Evidence lattice mismatch');
      return image;
    }
    function load(res,retry=false){
      if(ready(res))return Promise.resolve(true);
      if(pending[res])return pending[res];
      if(errors[res]&&!retry)return Promise.resolve(false);
      const record=tier(res);if(!record)return Promise.resolve(false);
      delete errors[res];
      pending[res]=Promise.all([
        pinned(record.path,record.sha256),
        pinned('cluster_results/ca/gettingeven_grid_'+res+'m.png',record.category_sha256)
      ]).then(([counts,categories])=>{
        aligned(counts,res,record);aligned(categories,res,record);
        for(let k=0;k<counts.d.length;k+=4){
          if(![0,255].includes(counts.d[k+3])||counts.d[k+3]!==categories.d[k+3]||categories.d[k+3]===255&&categories.d[k]>5&&categories.d[k]!==255)throw Error('Evidence category mask mismatch');
        }
        cache[res]={counts,categories};return true;
      }).catch(()=>{errors[res]=true;return false;}).finally(()=>{delete pending[res];onChange(res,'loaded');});
      onChange(res,'loading');return pending[res];
    }
    return{
      ready,state,load,
      cutoff:()=>cutoff,
      restore(value){cutoff=tier(5000)||tier(25000)?GettingEvenEvidence.cutoff(value):1;},
      choose(value,res){if(!active()||!GettingEvenEvidence.cutoffs.includes(+value)||+value!==1&&!ready(res))return false;cutoff=+value;return true;},
      countAt(res,col,row){return GettingEvenEvidence.count(cache[res]?.counts,col,row);},
      categoryImage:res=>cache[res]?.categories||null,
      visible(res,col,row){const count=this.countAt(res,col,row);return cutoff===1||count!==null&&count>=cutoff;},
      opacityExpression(opacity){return cutoff===1?opacity:['case',['>=',['coalesce',['get','geCount'],-1],cutoff],opacity,0];}
    };
  }
};
