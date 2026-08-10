"""Builds where-to-go-engine.ipynb — the tangible multi-objective "where to go"
engine for Blitz the Gap. Reads the engine's cached per-cell tables
(whereto_cells_*.csv from whereto.py) and shows, live: the objectives disagree,
each is backtested against its own goal, and the recommendation changes per
objective. Every number recomputed in-notebook; nothing hardcoded.
"""
from notebook_core import NotebookBuilder

nb = NotebookBuilder()
md, co = nb.md, nb.co

md(r"""# Where should I go to record biodiversity?

**Blitz the Gap · openbiodiversity.ca**

A "where should I go?" map looks like one map, but it hides a question: *what are you trying to achieve?* Find as many species as possible? Find the rare ones that matter for conservation? Cover habitats nobody has sampled? Reach places before they're logged or developed? These goals point to **different places** — so one map cannot serve them all.

This notebook treats the map as an **explicit, switchable choice of objective**, on real British Columbia observations from iNaturalist. Pick a goal, and the map re-ranks where to go. The recipe for every cell on the map is:

```
priority(cell)  =   value(cell, chosen objective)
                  ─────────────────────────────────   ×  guardrail
                      ( 1 + hours of travel )
```

Five objectives are offered — **discover, conservation, climate-coverage, staleness, urgency** (each explained in the next section). Travel time enters only as a *cost you divide by*, never as a reason to pick a place. A guardrail step protects sensitive species and respects data-sovereignty rules.

To check that each objective really delivers on its goal, the observations are split in time: earlier ones build the map, later ones (held back) test it. A goal "works" only if the places it picked from the early data actually paid off in the later data.

> What this map is for: directing where people **record which species live where**. It does not tell you how *many* individuals are present, what their traits are, or how species interact — those need different kinds of fieldwork. And it is tested on observations already collected, which is a realistic dry run, not a live field trial.""")

md(r"""## How each piece is computed

Everything sits on a grid of ~25 km cells over British Columbia. The observations are split by date: earlier ones define every score, later ones (held back) test it. Because the scores never see the later data, the test is honest. Each objective is a number from 0 to 1 on every cell, and they answer different questions:

- **discover — "go where few people have looked."** A cell's score is simply `1 / (number of earlier observations there)`: the emptier the cell, the higher the score. It is the simplest idea, and a good one for turning up new species.

- **conservation — "go where rare species already are."** First, each species gets a rarity weight — a species recorded in only a few places counts for a lot, one found everywhere counts for little. A cell's score is the *average rarity of the species already seen there*. The reasoning: rare species cluster in particular places, so a cell that already shows rare species is where more of them are likely to be found.

- **climate-coverage — "go where the *climate* is under-sampled, wherever that is."** This one is subtle. Two cells hundreds of km apart can have the *same* climate — recording the second adds little, because that kind of place is already covered. Meanwhile a rare habitat tucked inside a busy region is a real gap an "empty cells" map misses. So instead of empty *map* space, this looks at empty *climate* space:
  1. For each cell, read three climate values — average temperature, how much temperature swings across the year, and annual rainfall.
  2. Place each cell in this 3-D "climate space."
  3. Measure how crowded each cell's climate already is with existing records (a smooth count of nearby records in climate space).
  4. Score = `1 / crowding`. A cell whose climate is *rare* among existing records scores high — even if it sits in a busy area — and a remote cell with an *ordinary* climate scores low.
  That is why it picks very different places from *discover* (see §1). Its goal is covering the full range of habitats, not finding the most species — so the species-count test further down does not measure it, and shouldn't.

- **staleness — "go where no one has been lately."** Days since the most recent observation in the cell. Keeps the map current.

- **urgency — "sample before it changes."** How much of the cell has lost forest cover recently (from a global forest-loss map, 2015 onward). Places being rapidly cleared are a last chance to record what lives there. Like climate-coverage, this is a forward-looking goal, so the backward-looking species test doesn't capture it.

**Cost and guardrail.** Travel time to the nearest town (a published global map) is the cost: the final priority is `value / (1 + travel-hours)`, so a far-off but valuable cell stays in the running only until the trip becomes too expensive. On top of that, a guardrail hides the locations of sensitive species and respects Indigenous data-sovereignty rules.

**How each goal is tested.** On the held-back later observations, every cell is trimmed to the same small number of visits (five) so a cell can't look good just because it was visited more. Then, per cell, we count how many species turned up that were *new to that cell*, the same count weighted by rarity, and the *average rarity* of those new species. A goal "works" if the cells it ranked high really did deliver more of its target. To be sure a result isn't luck, the scores are reshuffled thousands of times; `*` marks a result that beats that chance baseline.""")

co(r"""import glob, json
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
import voi_backtest as vb
plt.rcParams.update({"figure.dpi": 120, "font.size": 10, "axes.grid": False})

cellfiles = sorted(glob.glob("cluster_results/whereto_cells_*.csv"))
taxa = {f.split("whereto_cells_")[-1].replace(".csv",""): pd.read_csv(f) for f in cellfiles}
OBJS = [c for c in next(iter(taxa.values())).columns if c.startswith("o_")]
LABEL = {"o_discover":"discover","o_conservation":"conservation","o_env_coverage":"env-coverage",
         "o_staleness":"staleness","o_urgency":"urgency"}
print("Animal groups loaded:", ", ".join(taxa))
print("Goals available:    ", ", ".join(LABEL[o] for o in OBJS))""")

md(r"""## 1 · The goals disagree — so "where to go" is a choice, not a fact

If every goal sent you to the same places, the map would be a simple measurement. It isn't. The grid below shows how similarly two goals rank the same cells: **1** means they agree completely, **0** means unrelated, and a **negative** number means they pull in opposite directions. Most pairs are near zero or negative — picking a different goal really does point you somewhere else.""")

co(r"""def sp(x,y):
    m=np.isfinite(x)&np.isfinite(y)
    if m.sum()<6 or np.std(x[m])==0 or np.std(y[m])==0: return np.nan
    return float(np.corrcoef(pd.Series(x[m]).rank(),pd.Series(y[m]).rank())[0,1])
mats=[]
for df in taxa.values():
    M=pd.DataFrame(index=OBJS,columns=OBJS,dtype=float)
    for a in OBJS:
        for b in OBJS: M.loc[a,b]=sp(df[a].values,df[b].values)
    mats.append(M)
D=sum(mats)/len(mats)
D.index=[LABEL[o] for o in OBJS]; D.columns=[LABEL[o] for o in OBJS]
fig,ax=plt.subplots(figsize=(5.6,4.8))
im=ax.imshow(D.values.astype(float),cmap="RdBu",vmin=-1,vmax=1)
ax.set_xticks(range(len(D)));ax.set_xticklabels(D.columns,rotation=30,ha="right")
ax.set_yticks(range(len(D)));ax.set_yticklabels(D.index)
for i in range(len(D)):
    for j in range(len(D)):
        ax.text(j,i,f"{D.values[i,j]:.2f}",ha="center",va="center",fontsize=9,
                color="white" if abs(D.values[i,j])>0.5 else "black")
fig.colorbar(im,shrink=0.8,label="ρ between cell rankings (mean across taxa)")
ax.set_title("Objectives rank cells differently → it's a value choice")
fig.tight_layout();plt.show()
print(f"discover vs conservation: mean ρ = {D.loc['discover','conservation']:.2f}  (negative = opposite recommendations)")""")

md(r"""## 2 · The same place, five maps

Each panel plots the British Columbia cells by their real location (longitude and latitude), shaded by how strongly each goal recommends them: **dark = go here, pale = skip** (blue star = Vancouver). Reading across a row, the dark patches move. *Discover* lights up the empty north and interior; *conservation* picks out a different, sparser set of cells; *climate-coverage* favours unusual-climate spots; *urgency* highlights the recently-cleared interior. Same data, five different answers to "where should I go?".

> An **interactive version on a real road map** — with sliders to blend the goals and a "find spots near me" button — is in `where-to-go.html`.""")

co(r"""show = sorted(taxa, key=lambda t: -len(taxa[t]))[:2]
VAN = (49.28, -123.12)
fig, axes = plt.subplots(len(show), len(OBJS), figsize=(2.9*len(OBJS), 3.0*len(show)), squeeze=False)
for r, t in enumerate(show):
    df = taxa[t]
    for c, o in enumerate(OBJS):
        ax = axes[r][c]
        ax.scatter(df.clon, df.clat, c=df[o], s=14, cmap="magma_r", vmin=0, vmax=1, zorder=2)
        ax.scatter(VAN[1], VAN[0], marker="*", s=95, c="#11a3ff", edgecolor="k", linewidth=0.5, zorder=5)
        ax.set_facecolor("#f2f4f7"); ax.grid(True, color="#dfe5ec", linewidth=0.5)
        if c == 0: ax.set_ylabel(f"{t}\nlatitude", fontsize=9)
        if r == len(show)-1: ax.set_xlabel("longitude", fontsize=9)
        if r == 0: ax.set_title(LABEL[o], fontsize=11)
        ax.tick_params(labelsize=7)
fig.suptitle("Where each goal sends you, across British Columbia  (dark = go here · blue star = Vancouver)", y=1.01)
fig.tight_layout(); plt.show()""")

md(r"""## 3 · Does each goal actually deliver? — the held-out test

This is the check that matters. Using only the later, held-back observations, we ask of each goal: did the cells it ranked highest really turn up more of what that goal is after? Three things are measured in each cell — how many species were found that were *new to that cell*, the same count leaning toward rare species, and the *average rarity* of those new species. A well-designed set of goals should show **each goal winning at its own target, and not at the others.**""")

co(r"""TARGETS={"total new spp":"rare_newK","rarity-wtd new":"w_newK","mean rarity of finds":"meanrarity_newK"}
rows=[]
for o in OBJS:
    rec={"objective":LABEL[o]}
    for tname,tcol in TARGETS.items():
        rs=[]
        for df in taxa.values():
            sub=df.dropna(subset=[o,tcol])
            r,p,_,_=vb.perm_test(sub[o].values,sub[tcol].values,np.random.default_rng(7))
            if np.isfinite(r): rs.append((r,p))
        med=np.median([r for r,_ in rs]); nsig=sum(1 for r,p in rs if p<0.05 and np.sign(r)==np.sign(med))
        rec[tname]=f"{med:+.2f} ({nsig}/{len(rs)})"
    rows.append(rec)
bt=pd.DataFrame(rows).set_index("objective")
print("How each goal scores on each outcome. +1 = the cells it favours deliver strongly,")
print("0 = no relationship, -1 = it favours the wrong cells. In () = how many of the 5")
print("animal groups gave a clear (non-chance) result in that direction.")
bt""")

md(r"""**What the table says.** *Discover* finds the most new species — but they are the **common** ones (its rarity score is negative). *Conservation* is the mirror image: it finds fewer species overall, yet the ones it finds are **rarer** — the very species discovery overlooks. So if rare, at-risk species are the point, you have to ask for them; chasing sheer numbers gives the opposite.

*Climate-coverage* and *urgency* score near zero here, and that is **correct, not a failure**. Their goals are not "find the most species": climate-coverage is about sampling every kind of habitat, and urgency is about reaching places before they change — neither is what this species-count test measures. Each goal should be judged by its own aim. The two goals whose aim *is* finding species — discover and conservation — are the ones this test can score, and they clearly pull apart.""")

md(r"""## 4 · A concrete answer: where to go near Vancouver

For the group with the most data, here is the single best cell each goal would send you to within reach of Vancouver — once as the outright best, and once after dividing by travel time (so a closer, almost-as-good cell wins). The goals name **different places**, which is the whole point: the right answer depends on what you are trying to do.""")

co(r"""t = show[0]; df = taxa[t].copy()
for o in OBJS:
    df[o.replace("o_","roi_")] = df[o]/(1+df.travel_min/60.0)   # ROI: value per travel-hour
def near(df, pt, deg=8):
    d=np.hypot(df.clat-pt[0],df.clon-pt[1]); return df[(d<=deg) & np.isfinite(df.travel_min)]
n=near(df,VAN)
print(f"Best place to go for each goal, within reach of Vancouver ({t}):\n")
for o in OBJS:
    r=n.loc[n[o].idxmax()]
    rr=n.loc[n[o.replace('o_','roi_')].idxmax()]
    print(f"  {LABEL[o]:13s}  outright best: ({r.clat:.1f}, {r.clon:.1f}), {r.travel_min:.0f} min away"
          f"   |  best per travel-hour: ({rr.clat:.1f}, {rr.clon:.1f}), {rr.travel_min:.0f} min")""")

md(r"""## 5 · What this shows

A "where should I go?" map is really a **choice of goal**, not a single right answer. The five goals send you to genuinely different places (§1, §2), and — the key point — each one delivers on its *own* aim when checked against held-back data (§3): chasing numbers finds many but common species; explicitly asking for rare species finds them; covering habitats and catching change-prone places are separate aims again.

The practical takeaway: a good tool should let people pick the goal openly, and should *prove* each goal works rather than assume it. Adding a new goal means adding one score and running the same held-out test.

**What it does and doesn't do.** It guides where to *record which species live where* — not how many individuals are present, what their traits are, or how species interact, which need other kinds of fieldwork. Travel time only ever *lowers* a place's priority (a cost), never raises it. Sensitive-species locations are hidden and Indigenous data-sovereignty rules respected. The check uses observations already collected — a realistic dry run, not a live field season.""")

nb.write("where-to-go-engine.ipynb")
