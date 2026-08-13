#!/bin/bash
#SBATCH --account=rrg-bengioy-ad_gpu
#SBATCH --gpus-per-node=h100:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=00:45:00
#SBATCH --output=/home/wietze/btg_fir/out/slurm-%j.out

# BTG design-04 discovery experiment — cross-cluster repro on Fir (DRAC H100).
# Confirms the Mila finding (spatial coverage >= embedding-novelty for discovery)
# on different hardware. That holds for Amphibia, which is what
# exp_discovery_acquisition.py hardcodes and so all this script can produce. It does
# not generalise: across the 7-taxon sweep in cluster_results/, embedding novelty
# wins on the species-rich taxa by wide margins. See research/README.md.
# Fir compute nodes have internet, so the script pulls
# iNat photos + model weights live; no pre-staging needed.
module purge
module load StdEnv/2023 python/3.11 cuda/12.2
source ~/btg_fir/env/bin/activate

echo "host $(hostname) | $(date)"
python -c "import torch; print('gpu', torch.cuda.get_device_name(0))"

cd ~/btg_fir
# primary backbone first, so a partial run still yields the headline result
for bb in dinov2 resnet50 clip; do
  echo "============ backbone: $bb ============"
  python exp_discovery_acquisition.py --backbone "$bb" --n 1200 --budget 300 --seeds 20 --out ~/btg_fir/out
done
echo "ALL DONE $(date)"
