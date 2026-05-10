cd /opt/agent-system/backend (carpeta donde esta el sistema)
sudo -u agentsys -H bash (pasa de usuario normal a agentsys)
source venv/bin/activate (activa el entorno virtual)
exit (salir de usuario agentsys)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload (levanta el servidor)
pkill -f uvicorn (lo elimina)


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