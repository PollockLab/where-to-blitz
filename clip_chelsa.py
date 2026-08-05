import numpy as np
import rasterio
from rasterio.windows import from_bounds

BBOX=(-139.0,48.0,-114.0,60.0)  # BC: minlon,minlat,maxlon,maxlat
base="/vsicurl/https://os.zhdk.cloud.switch.ch/chelsav2/GLOBAL/climatologies/1981-2010/bio/CHELSA_bio{}_1981-2010_V.2.1.tif"
bands={1:"temp",4:"seasonality",12:"precip"}
stack=[]; prof=None
for n in bands:
    with rasterio.open(base.format(n)) as ds:
        w=from_bounds(*BBOX, ds.transform)
        a=ds.read(1, window=w)
        sc,off=ds.scales[0],ds.offsets[0]
        a=a.astype("float32")*sc+off
        stack.append(a)
        if prof is None:
            prof=ds.profile.copy(); t=ds.window_transform(w)
            prof.update(count=3,dtype="float32",height=a.shape[0],width=a.shape[1],transform=t,driver="GTiff",compress="deflate")
    print("got bio",n,bands[n],"shape",a.shape,"range",round(float(np.nanmin(a)),1),round(float(np.nanmax(a)),1),flush=True)
with rasterio.open("cluster_results/bc_bioclim.tif","w",**prof) as dst:
    for i,a in enumerate(stack,1): dst.write(a,i)
print("wrote cluster_results/bc_bioclim.tif",flush=True)
