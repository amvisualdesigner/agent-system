cd /opt/agent-system/backend (carpeta donde esta el sistema)
sudo -u agentsys -H bash (pasa de usuario normal a agentsys)
source venv/bin/activate (activa el entorno virtual)
exit (salir de usuario agentsys)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload (levanta el servidor)
pkill -f uvicorn (lo elimina)

rm -rf /tmp/agent-runs/*
git worktree prune


curl -X POST http://0.0.0.0:8000/agent/run \
  -H "Content-Type: application/json" \
  -d @- <<EOF
{
  "task": "Edita test.ts y añade después de 'Hello, world!' la frase 'That is all'.",
  "scope": [],
  "constraints": [],
  "repo_context": []
}
EOF

# Initial security test
python -m app.__tests__.policy_tests


🔵 convertir policy en “deterministic validator + schema layer”

y añadir:

límites de operaciones por tipo
validación de estructura del diff (no solo existencia)
protección contra overwrite masivo

# Limpiar
python backend/app/maintenance/cleanup_agent_runs.py