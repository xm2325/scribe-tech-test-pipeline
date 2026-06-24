#!/usr/bin/env bash
#SBATCH --job-name=main500_v4q
#SBATCH --partition=gpuL
#SBATCH --gres=gpu:l40s:1
#SBATCH --time=01:00:00
#SBATCH --mem=64G
#SBATCH --cpus-per-task=8
#SBATCH --output=/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier/logs/main500_v4q.%A_%a.out
#SBATCH --error=/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier/logs/main500_v4q.%A_%a.err

set -Eeuo pipefail

TOP="/mnt/iusers01/sk01/f70829xm/code/nhm"
BLOCK="/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier"
MANIFEST="${BLOCK}/00_manifest/main500_local_images.tsv"

ENV_PREFIX="${TOP}/envs/nhm_scribe_yoloe_qwen_py311"
MAMBA="/mnt/iusers01/sk01/f70829xm/code/taylor/envs/bin/micromamba"
export MAMBA_ROOT_PREFIX="${TOP}/envs/micromamba_root"

WEIGHTS="/mnt/eps01-rds/Turing_AI_Fellowship/f70829xm/nhm_scribe_techtest_20260621/04_work/model_cache/yoloe/yoloe-26s-seg.pt"
RAW_SCRIPT="${BLOCK}/scripts/yoloe26_raw_boxes_one_image_v4_auxprompts.py"
POSTPROCESS_SCRIPT="${BLOCK}/scripts/boxlogic_v4_auxbarrier_rescue.py"

TASK_ID="${SLURM_ARRAY_TASK_ID}"

SID="$(awk -F'\t' -v tid="${TASK_ID}" 'NR==1{for(i=1;i<=NF;i++) if($i=="specimen_id") c=i} NR>1 && $1==tid{print $c; exit}' "${MANIFEST}")"

if [ -z "${SID}" ]; then
  echo "[FAIL] Could not resolve specimen_id for TASK_ID=${TASK_ID}"
  exit 2
fi

SAFE_SID="$(python - <<PY
s="${SID}"
print("".join(c if c.isalnum() or c in "._-" else "_" for c in s).strip("_") or "unknown")
PY
)"

RAW_DIR="${BLOCK}/01_raw_yoloe"
BOXLOGIC_DIR="${BLOCK}/02_boxlogic"
RAW_TSV="${RAW_DIR}/${SAFE_SID}/raw_yoloe_boxes.tsv"
FINAL_QUEUE="${BOXLOGIC_DIR}/${SAFE_SID}/05_final_queue/final_ocr_queue.tsv"
AUX_TSV="${BOXLOGIC_DIR}/${SAFE_SID}/04_candidate_panels/auxiliary_barrier_panels.tsv"
MARKER="${BLOCK}/markers/${SAFE_SID}.V4_AUXBARRIER.PASS"

echo "HOST=$(hostname)"
echo "DATE=$(date)"
echo "TASK_ID=${TASK_ID}"
echo "SID=${SID}"
echo "SAFE_SID=${SAFE_SID}"
echo "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-}"
echo "RAW_TSV=${RAW_TSV}"
echo "FINAL_QUEUE=${FINAL_QUEUE}"
echo "AUX_TSV=${AUX_TSV}"

if [ -s "${FINAL_QUEUE}" ] && [ -s "${MARKER}" ]; then
  echo "[SKIP] final queue already exists and marker present."
  exit 0
fi

for f in "${MANIFEST}" "${RAW_SCRIPT}" "${POSTPROCESS_SCRIPT}" "${WEIGHTS}"; do
  if [ ! -s "${f}" ]; then
    echo "[FAIL] missing required file: ${f}"
    exit 3
  fi
done

python -m py_compile "${RAW_SCRIPT}"
python -m py_compile "${POSTPROCESS_SCRIPT}"

eval "$("${MAMBA}" shell hook -s bash)"
micromamba activate "${ENV_PREFIX}"

cd "${TOP}/nhm_scribe_repo"

mkdir -p "${BLOCK}/.ultralytics/Ultralytics"
export YOLO_CONFIG_DIR="${BLOCK}/.ultralytics"
export MPLBACKEND=Agg
export QT_QPA_PLATFORM=offscreen
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

python - <<'PY'
import torch
import ultralytics
print("torch", torch.__version__)
print("cuda", torch.cuda.is_available())
print("cuda_device_count", torch.cuda.device_count())
print("ultralytics", ultralytics.__version__)
PY

echo
echo "[STEP 1] YOLOE raw boxes with text + auxiliary prompts"
python "${RAW_SCRIPT}" \
  --manifest "${MANIFEST}" \
  --task-id "${TASK_ID}" \
  --weights "${WEIGHTS}" \
  --out-root "${RAW_DIR}" \
  --imgsz 1024 \
  --conf 0.01 \
  --iou 0.90 \
  --max-det 1000 \
  --device 0

echo
echo "[STEP 2] v4 auxbarrier + all uncovered text rescue"
python "${POSTPROCESS_SCRIPT}" \
  --input-tsv "${RAW_TSV}" \
  --outdir "${BOXLOGIC_DIR}" \
  --coverage-target 0.98

if [ ! -s "${FINAL_QUEUE}" ]; then
  echo "[FAIL] final queue missing or empty: ${FINAL_QUEUE}"
  exit 4
fi

mkdir -p "${BLOCK}/markers"
touch "${MARKER}"

echo
echo "[PASS] ${SID}"
echo "Final queue:"
wc -l "${FINAL_QUEUE}"
awk -F'\t' 'NR>1{a[$4]++} END{for(k in a) print k,a[k]}' "${FINAL_QUEUE}" | sort || true

echo
echo "Auxiliary barrier panels:"
if [ -s "${AUX_TSV}" ]; then
  wc -l "${AUX_TSV}"
else
  echo "No auxiliary barrier table"
fi

echo
echo "Overlay:"
ls -lh "${BOXLOGIC_DIR}/${SAFE_SID}/05_final_queue/final_queue_overlay.png" || true

echo
echo "Crop count:"
find "${BOXLOGIC_DIR}/${SAFE_SID}/05_final_queue/crops" -type f -name "*.jpg" | wc -l
