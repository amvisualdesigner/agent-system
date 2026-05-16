#!/bin/bash
set -e

BACKUP_ROOT="/opt/agent-backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DEST="$BACKUP_ROOT/backup_$TIMESTAMP"

# Docker compose compatibility (v1 standalone vs v2 plugin)
if command -v docker-compose &>/dev/null; then
  DC="docker-compose"
else
  DC="$DC"
fi

# Prevent accidental backup to system paths
EXCLUDE_PATHS=("/var/lib/docker" "/run/containerd" "/opt/containerd")
for p in "${EXCLUDE_PATHS[@]}"; do
  if [[ "$DEST" == "$p"* ]]; then
    echo "ERROR: Backup destination under excluded path: $p"
    exit 1
  fi
done

cd "$(dirname "$0")/.."

echo "======================================"
echo " BACKUP START: $TIMESTAMP"
echo "======================================"

# =============================
# 0. PREP DIR
# =============================
mkdir -p "$DEST"

# =============================
# 1. STOP SYSTEM (CONSISTENCY POINT)
# =============================
echo "[1/5] Stopping Docker stack..."
$DC down

# =============================
# 2. DOCKER STATE
# =============================
echo "[2/5] Saving compose state..."
cp docker-compose.yml "$DEST/"
cp .env "$DEST/" 2>/dev/null || true

docker ps -a > "$DEST/docker_ps.txt"

# =============================
# 3. BACKEND + ORCHESTRATOR CODE (CLEAN)
# =============================
echo "[3/5] Backing up code (clean)..."

mkdir -p "$DEST/code"

# Backend (sin venv, sin basura)
rsync -av \
  --exclude "venv" \
  --exclude "venv.quarantine" \
  --exclude "__pycache__" \
  backend/ "$DEST/code/backend/"

# Orchestrator (SOLO código, NO runs duplicados)
rsync -av \
  --exclude "__pycache__" \
  orchestrator/ "$DEST/code/orchestrator/"

# UI
rsync -av ui/ "$DEST/code/ui/" 2>/dev/null || true

# =============================
# 4. DATA LAYER (REAL DATA ONLY)
# =============================
echo "[4/5] Backing up persistent data..."

# REPOS + WORKTREES + ARTIFACTS
tar -czf "$DEST/agent-repos.tar.gz" /opt/agent-repos

# ORCHESTRATOR RUNS (SOLO UNA VEZ)
tar -czf "$DEST/orchestrator-runs.tar.gz" orchestrator/runs

# DOCKER VOLUMES
docker run --rm \
  -v hf_cache:/data \
  -v "$DEST":/backup \
  alpine \
  tar czf /backup/hf_cache.tar.gz -C /data .

# =============================
# 5. METADATA
# =============================
echo "[5/5] Writing manifest..."

cat <<EOF > "$DEST/manifest.json"
{
  "timestamp": "$TIMESTAMP",
  "mode": "cold_backup",
  "services": [
    "vllm",
    "backend",
    "orchestrator",
    "ui"
  ],
  "notes": "full system consistent snapshot with docker down"
}
EOF

# =============================
# RESTART SYSTEM
# =============================
echo "Restarting system..."
$DC up -d

echo "======================================"
echo " BACKUP COMPLETED"
echo " LOCATION: $DEST"
echo "======================================"

# =============================
# CLEAN OLD BACKUPS
# =============================
cd "$BACKUP_ROOT"
ls -1t | tail -n +8 | xargs -r rm -rf