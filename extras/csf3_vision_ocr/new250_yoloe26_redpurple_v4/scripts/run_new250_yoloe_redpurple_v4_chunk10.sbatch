#!/usr/bin/env bash
#SBATCH --job-name=new250v4c10
#SBATCH --partition=gpuL
#SBATCH --gres=gpu:l40s:1
#SBATCH --time=02:00:00
#SBATCH --mem=80G
#SBATCH --cpus-per-task=4
#SBATCH --output=/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/logs/new250v4c10.%A_%a.out
#SBATCH --error=/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/logs/new250v4c10.%A_%a.err

set -Eeuo pipefail

TOP="/mnt/iusers01/sk01/f70829xm/code/nhm"
BLOCK="/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier"
MANIFEST="${BLOCK}/00_manifest/new250_local_images.tsv"

ENV_PREFIX="${TOP}/envs/nhm_scribe_yoloe_qwen_py311"
MAMBA="/mnt/iusers01/sk01/f70829xm/code/taylor/envs/bin/micromamba"
export MAMBA_ROOT_PREFIX="${TOP}/envs/micromamba_root"

WEIGHTS="/mnt/eps01-rds/Turing_AI_Fellowship/f70829xm/nhm_scribe_techtest_20260621/04_work/model_cache/yoloe/yoloe-26s-seg.pt"

RAW_CHUNK_SCRIPT="${BLOCK}/scripts/yoloe26_raw_boxes_chunk_v4_auxprompts.py"
POSTPROCESS_SCRIPT="${BLOCK}/scripts/boxlogic_v4_auxbarrier_rescue.py"

CHUNK_ID="${SLURM_ARRAY_TASK_ID}"
CHUNK_SIZE="${CHUNK_SIZE:-10}"

RAW_DIR="${BLOCK}/01_raw_yoloe"
BOXLOGIC_DIR="${BLOCK}/02_boxlogic"
CHUNK_MARKER="${BLOCK}/markers/chunk_${CHUNK_ID}.V4_AUXBARRIER.CHUNK10.PASS"

echo "HOST=$(hostname)"
echo "DATE=$(date)"
echo "CHUNK_ID=${CHUNK_ID}"
echo "CHUNK_SIZE=${CHUNK_SIZE}"
echo "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-}"

for f in "${MANIFEST}" "${RAW_CHUNK_SCRIPT}" "${POSTPROCESS_SCRIPT}" "${WEIGHTS}"; do
  if [ ! -s "${f}" ]; then
    echo "[FAIL] missing required file: ${f}"
    exit 3
  fi
done

python -m py_compile "${RAW_CHUNK_SCRIPT}"
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
echo "[STEP 1] YOLOE raw boxes for chunk ${CHUNK_ID}"
python "${RAW_CHUNK_SCRIPT}" \
  --manifest "${MANIFEST}" \
  --chunk-id "${CHUNK_ID}" \
  --chunk-size "${CHUNK_SIZE}" \
  --weights "${WEIGHTS}" \
  --out-root "${RAW_DIR}" \
  --imgsz 1024 \
  --conf 0.01 \
  --iou 0.90 \
  --max-det 1000 \
  --device 0

echo
echo "[STEP 2] v4 boxlogic for each image in chunk"

START=$((CHUNK_ID * CHUNK_SIZE))
END=$((START + CHUNK_SIZE - 1))

FAIL_COUNT=0
PASS_COUNT=0
SKIP_COUNT=0

for TASK_ID in $(seq "${START}" "${END}"); do
  LINE_NO=$((TASK_ID + 2))
  ROW="$(awk -F'\t' -v line="${LINE_NO}" 'NR==line{print; exit}' "${MANIFEST}")"

  if [ -z "${ROW}" ]; then
    echo "[INFO] no manifest row for TASK_ID=${TASK_ID}; stop chunk loop"
    continue
  fi

  SID="$(awk -F'\t' -v line="${LINE_NO}" 'NR==1{for(i=1;i<=NF;i++) if($i=="specimen_id") c=i} NR==line{print $c; exit}' "${MANIFEST}")"

  SAFE_SID="$(python - <<PY
s="${SID}"
print("".join(c if c.isalnum() or c in "._-" else "_" for c in s).strip("_") or "unknown")
PY
)"

  RAW_TSV="${RAW_DIR}/${SAFE_SID}/raw_yoloe_boxes.tsv"
  FINAL_QUEUE="${BOXLOGIC_DIR}/${SAFE_SID}/05_final_queue/final_ocr_queue.tsv"
  MARKER="${BLOCK}/markers/${SAFE_SID}.V4_AUXBARRIER.PASS"

  echo
  echo "---- TASK_ID=${TASK_ID} SID=${SID} SAFE_SID=${SAFE_SID}"

  if [ -s "${FINAL_QUEUE}" ] && [ -s "${MARKER}" ]; then
    echo "[SKIP] existing final queue and marker"
    SKIP_COUNT=$((SKIP_COUNT + 1))
    continue
  fi

  if [ ! -s "${RAW_TSV}" ]; then
    echo "[FAIL] missing raw TSV: ${RAW_TSV}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    continue
  fi

  set +e
  python "${POSTPROCESS_SCRIPT}" \
    --input-tsv "${RAW_TSV}" \
    --outdir "${BOXLOGIC_DIR}" \
    --coverage-target 0.98
  RC=$?
  set -e

  if [ "${RC}" -ne 0 ]; then
    echo "[FAIL] postprocess failed RC=${RC} SID=${SID}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    continue
  fi

  if [ ! -s "${FINAL_QUEUE}" ]; then
    echo "[FAIL] final queue missing or empty: ${FINAL_QUEUE}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    continue
  fi

  touch "${MARKER}"
  PASS_COUNT=$((PASS_COUNT + 1))

  echo "[PASS] ${SID}"
  echo "Final queue rows:"
  wc -l "${FINAL_QUEUE}"
  awk -F'\t' 'NR>1{a[$4]++} END{for(k in a) print k,a[k]}' "${FINAL_QUEUE}" | sort || true
done

mkdir -p "${BLOCK}/markers"
echo "PASS_COUNT=${PASS_COUNT}" > "${CHUNK_MARKER}.tmp"
echo "SKIP_COUNT=${SKIP_COUNT}" >> "${CHUNK_MARKER}.tmp"
echo "FAIL_COUNT=${FAIL_COUNT}" >> "${CHUNK_MARKER}.tmp"
mv "${CHUNK_MARKER}.tmp" "${CHUNK_MARKER}"

echo
echo "Chunk summary:"
cat "${CHUNK_MARKER}"

if [ "${FAIL_COUNT}" -ne 0 ]; then
  echo "[REVIEW] chunk has failures"
  exit 5
fi

echo "[PASS] chunk ${CHUNK_ID}"
