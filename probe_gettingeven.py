"""Does the Getting Even floor put noise on the map?

`GE_MIN_RECORDS` is 1, so a cell holding one record gets a priority group even though
five of the six groups sit at a share of zero there. This measures whether those thin
cells are the ragged part of the 25 km map, and what pooling would cost if they were.

Three statistics, all at 25 km:

* how the records are distributed over the lattice;
* pooled priorities over 3x3, 5x5 and 7x7 windows, and how often the pooled answer
  still matches the unpooled one on cells that were never short of data;
* the share of cells whose priority differs from the majority of their eight
  neighbours, split by the cell's record count, against a permutation null that keeps
  the national mix of groups and destroys the spatial arrangement.

Run from the repo root, after a 25 km grid build:

    python probe_gettingeven.py
"""

import json

import numpy as np
import rasterio
import rasterio.warp
from numpy.lib.stride_tricks import sliding_window_view

D="cluster_results/ca"
idx=json.load(open(D+"/index.json")); ntr=idx["row_format"].index("n_train")
GE={"Fishes":["Actinopterygii"],"Fungi":["Fungi"],"Reptiles & Amphibians":["Amphibia","Reptilia"],
    "Invertebrates":["Arachnida","Insecta","Mollusca"],"Mammals":["Mammalia"],"Plants":["Plantae"]}
CATS=list(GE)
def col(g):
    r=json.load(open(D+"/"+idx["files"][g]))[g]; return np.array([x[ntr] for x in r],float)
tot=col("All biodiversity")
cnt=np.array([sum(col(g) for g in GE[c]) for c in CATS])
print("cells",len(tot))
bins=[0,1,3,10,30,100,300,1000,10**9]
for a,b in zip(bins,bins[1:]):
    m=(tot>=a)&(tot<b); print(f"  {a:>5}-{b if b<10**8 else 'inf':>6}: {m.sum():6d} cells {100*m.mean():5.1f}%  {tot[m].sum():12,.0f} records")
print("share of all records in cells with <30: %.3f%%"%(100*tot[tot<30].sum()/tot.sum()))
ds=rasterio.open(D+"/grid_25000m/All_biodiversity.tif")
cells=json.load(open(D+"/"+idx["files"]["All biodiversity"]))["All biodiversity"]
x,y=rasterio.warp.transform("EPSG:4326",ds.crs,[c[1] for c in cells],[c[0] for c in cells])
r,c=rasterio.transform.rowcol(ds.transform,x,y); r=np.asarray(r); c=np.asarray(c)
H,W=ds.shape
T=np.zeros((H,W)); T[r,c]=tot
C=np.zeros((len(CATS),H,W))
for i in range(len(CATS)): C[i][r,c]=cnt[i]
onmask=np.zeros((H,W),bool); onmask[r,c]=True
def pool(a,k):
    if k==1: return a.copy()
    h=k//2
    return sliding_window_view(np.pad(a,h),(k,k)).sum((-1,-2))
base=None
for k in (1,3,5,7):
    Tp=pool(T,k); Cp=np.array([pool(C[i],k) for i in range(len(CATS))])
    scored=onmask&(Tp>=1)
    prop=np.where(scored,Cp/np.where(Tp>0,Tp,1),np.nan)
    z=np.array([(prop[i]-np.nanmean(prop[i]))/np.nanstd(prop[i]) for i in range(len(CATS))])
    pri=np.where(scored,np.argmin(np.where(np.isfinite(z),z,np.inf),0),-1)
    u,cn=np.unique(pri[scored],return_counts=True)
    print(f"k={k} ({k*25} km window): scored {scored.sum():6d} ", {CATS[a]:int(b) for a,b in zip(u,cn)})
    if k==1:
        base=pri.copy(); scored1=scored.copy(); rich=onmask&(T>=100)
    else:
        m=rich&scored
        print(f"       agrees with unpooled on the {m.sum()} cells with >=100 records: {100*(pri[m]==base[m]).mean():.1f}%")
# --- speckle: does a cell's priority group disagree with its eight neighbours, and
# --- does that disagreement depend on how few records the cell holds?
NEIGH=[(dr,dc) for dr in (-1,0,1) for dc in (-1,0,1) if (dr,dc)!=(0,0)]
MIN_VOTERS=3          # below this the "majority" is itself a tie-break
votes=np.zeros((len(CATS),H,W))
nvote=np.zeros((H,W))
for dr,dc in NEIGH:
    sh_pri=np.roll(np.roll(base,dr,0),dc,1)
    sh_ok=np.roll(np.roll(scored1,dr,0),dc,1)
    # np.roll wraps; blank the wrapped edge so a cell never votes across the raster
    if dr: (sh_ok[:1] if dr>0 else sh_ok[-1:])[:]=False
    if dc: (sh_ok[:,:1] if dc>0 else sh_ok[:,-1:])[:]=False
    nvote+=sh_ok
    for i in range(len(CATS)):
        votes[i]+=sh_ok&(sh_pri==i)
majority=np.argmax(votes,0)
comparable=scored1&(nvote>=MIN_VOTERS)
disagree=comparable&(base!=majority)
print(f"\nspeckle: {comparable.sum()} scored cells have >={MIN_VOTERS} scored neighbours; "
      f"{disagree[comparable].sum() if comparable.any() else 0} differ from the neighbour majority "
      f"({100*disagree[comparable].mean():.1f}%)")
print("  records in cell   cells   differ from neighbour majority")
rb=[1,2,3,6,11,31,101,301,10**9]
for a,b in zip(rb,rb[1:]):
    m=comparable&(T>=a)&(T<b)
    if not m.any(): continue
    lab=f"{a}" if b==a+1 else (f"{a}+" if b>10**8 else f"{a}-{b-1}")
    print(f"  {lab:>15}  {m.sum():6d}   {100*disagree[m].mean():5.1f}%")

# A 6-category map disagrees with its own neighbours a lot by chance. Permute the
# priority labels over the scored cells, keeping the national mix, to get the floor.
rng=np.random.default_rng(0)
null=[]; nullcell=np.zeros((H,W))
for _ in range(50):
    perm=base.copy()
    lab=base[scored1].copy(); rng.shuffle(lab)
    perm[scored1]=lab
    v=np.zeros((len(CATS),H,W))
    for dr,dc in NEIGH:
        sp=np.roll(np.roll(perm,dr,0),dc,1); so=np.roll(np.roll(scored1,dr,0),dc,1)
        if dr: (so[:1] if dr>0 else so[-1:])[:]=False
        if dc: (so[:,:1] if dc>0 else so[:,-1:])[:]=False
        for i in range(len(CATS)): v[i]+=so&(sp==i)
    d=comparable&(perm!=np.argmax(v,0))
    null.append(d[comparable].mean()); nullcell+=d
null=np.array(null); nullcell/=len(null)
print(f"  permuted null (national mix, no spatial structure): {100*null.mean():.1f}% "
      f"+/- {100*null.std():.1f}%  ->  observed {100*disagree[comparable].mean():.1f}%")
# Per bin, because the low bins answer Plants or Invertebrates almost always, and two
# neighbours holding the same common answer agree for no ecological reason.
print("  records in cell   cells   observed   null   structure (null - observed)")
for a,b in zip(rb,rb[1:]):
    m=comparable&(T>=a)&(T<b)
    if not m.any(): continue
    lab=f"{a}" if b==a+1 else (f"{a}+" if b>10**8 else f"{a}-{b-1}")
    o=100*disagree[m].mean(); n=100*nullcell[m].mean()
    print(f"  {lab:>15}  {m.sum():6d}    {o:5.1f}%  {n:5.1f}%   {n-o:+5.1f} pts")

empty=onmask&(T<1)
print("empty cells:",int(empty.sum()))
for k in (3,5,7):
    print(f"   with a >=30-record cell inside {k}x{k}:", int((empty&(pool((T>=30).astype(float),k)>0)).sum()))
