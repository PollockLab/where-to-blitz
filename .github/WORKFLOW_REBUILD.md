# Rebuild and deploy

`rebuild-grid.yml`:

| Trigger | What runs |
|---|---|
| Pull request | Lint, shell syntax, pytest |
| Push to main | Those, then `deploy-site-fast`: rebuild `index.html`, redeploy, reuse the last release's tiles |
| Manual dispatch | `prepare-inputs` → `fit-25km` → `matrix-build` (11 groups, 5 km) → `aggregate` → `validate` → `publish-release` + `deploy-site` (needs `publish: true`). 2-3 h |

```bash
gh workflow run rebuild-grid.yml -f publish=true                 # build, publish, deploy
gh workflow run rebuild-grid.yml -f publish=false                # build only
gh workflow run rebuild-grid.yml -f validate_only=true -f artifact_name="rebuilt-site-<hash>"
```

Dispatch only when the grid or an input changed. Runs are keyed by an inputs hash (release
tag, `breaks.json`, commit of each critical-path script); an unchanged rerun restores the
previous artifact instead of rebuilding.

## What the app loads

Not tiles. `build_grid_values.py` writes one viridis PNG per (group, goal) to
`cluster_results/ca/values/` and the app paints cells from those pixels (#116). PMTiles are
the density overlay only, too big for git, so they live on the `grid-outputs-v1` release and
`deploy-site` copies them into `_site/tiles/`.

## Pages

`deploy-site` and `deploy-site-fast` both call `deploy-pages@v4`, so they share a
`concurrency: pages` group. Nothing goes live unless the repo's Pages source is GitHub
Actions:

```bash
gh api repos/PollockLab/where-to-blitz/pages --jq .build_type    # want: workflow
```

If a run fails, read the first failed job: a failed job uploads no artifact. Don't publish
past `validate` - it catches bad CRS, NaN pixels and tiers that stopped nesting.
