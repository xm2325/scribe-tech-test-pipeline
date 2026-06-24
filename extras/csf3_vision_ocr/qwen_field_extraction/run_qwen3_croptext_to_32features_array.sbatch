#!/usr/bin/env bash
#SBATCH --job-name=qwen37extract
#SBATCH --partition=gpuA
#SBATCH --gres=gpu:a100_80g:1
#SBATCH --time=04:00:00
#SBATCH --mem=100G
#SBATCH --cpus-per-task=12
#SBATCH --output=logs/qwen37extract.%A_%a.out
#SBATCH --error=logs/qwen37extract.%A_%a.err

set -Eeuo pipefail

: "${SCRIBE_QWEN_EXTRACT:=/net/scratch/f70829xm/qwen3_14b_ocr_to_37column_jsonfilled750_v1}"
EXTRACT="${SCRIBE_QWEN_EXTRACT}"
: "${SCRIBE_NHM_TOP:=/mnt/iusers01/sk01/f70829xm/code/nhm}"
TOP="${SCRIBE_NHM_TOP}"

CHUNK_ID="${SLURM_ARRAY_TASK_ID}"
CLIENT="${EXTRACT}/scripts/qwen3_ocr_to_37column_client.py"
CHUNK_INDEX="${EXTRACT}/02_chunks/ocr_to_37column_chunks.tsv"
OUT_ROOT="${EXTRACT}/03_outputs"

MODEL_ID="${MODEL_ID:-Qwen/Qwen3-14B}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-qwen3_text}"

: "${SCRIBE_MAMBA:=/mnt/iusers01/sk01/f70829xm/code/taylor/envs/bin/micromamba}"
MAMBA="${SCRIBE_MAMBA}"
ENV_PREFIX="${TOP}/envs/nhm_qwen3vl_vllm_py311"
export MAMBA_ROOT_PREFIX="${TOP}/envs/micromamba_root"

PORT="$((21000 + (${SLURM_ARRAY_TASK_ID} % 1000)))"
API_BASE="http://127.0.0.1:${PORT}/v1"

OUTDIR="${OUT_ROOT}/chunk_$(printf '%04d' "${CHUNK_ID}")"
SUMMARY="${OUTDIR}/qwen3_14b_ocr_to_37column_summary.json"
MARKER="${EXTRACT}/markers/chunk_${CHUNK_ID}.QWEN3_14B_OCR_TO_37COLUMN.PASS"

echo "DATE=$(date)"
echo "HOST=$(hostname)"
echo "CHUNK_ID=${CHUNK_ID}"
echo "MODEL_ID=${MODEL_ID}"
echo "PORT=${PORT}"
echo "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-}"

if [ -s "${MARKER}" ] && [ -s "${SUMMARY}" ]; then
  echo "[SKIP] existing marker and summary: ${MARKER}"
  exit 0
fi

for f in "${CLIENT}" "${CHUNK_INDEX}"; do
  if [ ! -s "${f}" ]; then
    echo "[FAIL] missing required file: ${f}"
    exit 3
  fi
done

eval "$("${MAMBA}" shell hook -s bash)"
micromamba activate "${ENV_PREFIX}"

python - <<'PY'
import sys, requests
print("python", sys.executable)
print("requests ok", requests.__version__)
PY

python -m py_compile "${CLIENT}"

source /etc/profile.d/modules.sh 2>/dev/null || true
module load cuda/12.6.2 >/dev/null 2>&1 || module load cuda/12.6 >/dev/null 2>&1 || module load cuda >/dev/null 2>&1 || true

export CUDA_HOME="/opt/apps/pkg/compilers/cuda/12.6.2"
export CUDA_PATH="${CUDA_HOME}"
export PATH="${CUDA_HOME}/bin:${PATH}"
export LD_LIBRARY_PATH="${CUDA_HOME}/lib64:${LD_LIBRARY_PATH:-}"

echo "CUDA_HOME=${CUDA_HOME}"
echo "nvcc=$(command -v nvcc || true)"
nvcc --version || true

mkdir -p "${OUTDIR}" "${EXTRACT}/logs" "${EXTRACT}/markers"

export HF_HOME="${EXTRACT}/hf_home"
export TRANSFORMERS_CACHE="${EXTRACT}/hf_home"
export VLLM_CACHE_ROOT="${EXTRACT}/vllm_cache"
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
export NUMEXPR_NUM_THREADS=4

SERVER_LOG="${EXTRACT}/logs/vllm_qwen3_14b.${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.log"

cleanup() {
  if [ -n "${SERVER_PID:-}" ]; then
    echo "Stopping vLLM server PID=${SERVER_PID}"
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo
echo "[STEP 1] Start vLLM server"

python -m vllm.entrypoints.openai.api_server \
  --model "${MODEL_ID}" \
  --served-model-name "${SERVED_MODEL_NAME}" \
  --host 127.0.0.1 \
  --port "${PORT}" \
  --trust-remote-code \
  --gpu-memory-utilization 0.85 \
  --max-model-len 16384 \
  > "${SERVER_LOG}" 2>&1 &

SERVER_PID=$!
echo "SERVER_PID=${SERVER_PID}"
echo "SERVER_LOG=${SERVER_LOG}"

echo
echo "[STEP 2] Wait for server"
READY=0
for i in $(seq 1 360); do
  if curl -fsS "${API_BASE}/models" >/dev/null 2>&1; then
    READY=1
    break
  fi
  if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    echo "[FAIL] vLLM server died"
    tail -n 180 "${SERVER_LOG}" || true
    exit 10
  fi
  sleep 5
done

if [ "${READY}" != "1" ]; then
  echo "[FAIL] vLLM server not ready"
  tail -n 220 "${SERVER_LOG}" || true
  exit 11
fi

echo "vLLM server ready"

echo
echo "[STEP 3] Run extraction client"

python "${CLIENT}" \
  --chunk-id "${CHUNK_ID}" \
  --chunk-index "${CHUNK_INDEX}" \
  --out-root "${OUT_ROOT}" \
  --api-base "${API_BASE}" \
  --model "${SERVED_MODEL_NAME}" \
  --temperature 0 \
  --max-tokens 8192 \
  --request-timeout 2000

echo
echo "[STEP 4] Audit summary"
cat "${SUMMARY}"

FAIL_COUNT="$(python - <<PY
import json
from pathlib import Path
p = Path("${SUMMARY}")
d = json.loads(p.read_text())
print(d.get("fail_count", 999999))
PY
)"

if [ "${FAIL_COUNT}" != "0" ]; then
  echo "[FAIL] fail_count=${FAIL_COUNT}"
  exit 20
fi

touch "${MARKER}"
echo "[PASS] ${MARKER}"
echo "DONE $(date)"
