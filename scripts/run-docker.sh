#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

: "${TOGETHER_API_KEY:?set TOGETHER_API_KEY first}"

IMAGE="${IMAGE:-python:3.12-alpine}"
CHANNEL="${CHANNEL:-13}"
SECONDS_CAP="${SECONDS_CAP:-180}"

BEN_MODEL="${BEN_MODEL:-openai/gpt-oss-20b}"
IVAN_MODEL="${IVAN_MODEL:-openai/gpt-oss-20b}"

agent_b64="$(base64 < agent.py | tr -d '\n')"

cleanup() {
  if [[ "${KEEP:-0}" != "1" ]]; then
    docker rm -f docktalk-ben docktalk-ivan >/dev/null 2>&1 || true
    docker network rm docktalk-ben-net docktalk-ivan-net >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

cleanup

docker network create docktalk-ben-net >/dev/null
docker network create docktalk-ivan-net >/dev/null

common_env=(
  -e "TOGETHER_API_KEY=${TOGETHER_API_KEY}"
  -e "AGENT_B64=${agent_b64}"
  -e "CHANNEL=${CHANNEL}"
  -e "SECONDS_CAP=${SECONDS_CAP}"
  -e "PYTHONUNBUFFERED=1"
)

docker run -d --name docktalk-ivan \
  --network docktalk-ivan-net \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges \
  "${common_env[@]}" \
  "$IMAGE" \
  sh -lc "printf '%s' \"\$AGENT_B64\" | base64 -d >/tmp/agent.py && exec python -u /tmp/agent.py --agent ivan --model '$IVAN_MODEL' --channel '$CHANNEL' --seconds '$SECONDS_CAP' --mode docker" >/dev/null

docker run -d --name docktalk-ben \
  --network docktalk-ben-net \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges \
  "${common_env[@]}" \
  "$IMAGE" \
  sh -lc "printf '%s' \"\$AGENT_B64\" | base64 -d >/tmp/agent.py && exec python -u /tmp/agent.py --agent ben --model '$BEN_MODEL' --channel '$CHANNEL' --seconds '$SECONDS_CAP' --mode docker" >/dev/null

echo "DockTalk docker run started: channel=${CHANNEL}, cap=${SECONDS_CAP}s"
echo "Following logs. Set KEEP=1 to leave containers behind."
echo

docker logs -f docktalk-ben &
ben_log_pid=$!
docker logs -f docktalk-ivan &
ivan_log_pid=$!

docker wait docktalk-ben docktalk-ivan >/dev/null || true
kill "$ben_log_pid" "$ivan_log_pid" >/dev/null 2>&1 || true
