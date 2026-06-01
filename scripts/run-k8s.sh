#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

: "${TOGETHER_API_KEY:?set TOGETHER_API_KEY first}"

IMAGE="${IMAGE:-python:3.12-alpine}"
CHANNEL="${CHANNEL:-13}"
SECONDS_CAP="${SECONDS_CAP:-180}"
NS="${NS:-docktalk-repro}"
NODE="${NODE:-}"

BEN_MODEL="${BEN_MODEL:-openai/gpt-oss-20b}"
IVAN_MODEL="${IVAN_MODEL:-google/gemma-4-31B-it}"

agent_b64="$(base64 < agent.py | tr -d '\n')"

if [[ -z "$NODE" ]]; then
  NODE="$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')"
fi

kubectl create ns "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "$NS" create secret generic together-key \
  --from-literal=TOGETHER_API_KEY="$TOGETHER_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

cat <<YAML | kubectl apply -f - >/dev/null
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: docktalk-deny-peer-ingress
  namespace: ${NS}
spec:
  podSelector:
    matchLabels:
      app: docktalk
  policyTypes:
    - Ingress
  ingress: []
---
apiVersion: v1
kind: Pod
metadata:
  name: docktalk-ivan
  namespace: ${NS}
  labels:
    app: docktalk
    agent: ivan
spec:
  restartPolicy: Never
  nodeName: ${NODE}
  volumes:
    - name: tmp
      emptyDir:
        medium: Memory
        sizeLimit: 16Mi
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: ivan
      image: ${IMAGE}
      imagePullPolicy: IfNotPresent
      command: ["sh", "-lc"]
      args:
        - "printf '%s' \"\$AGENT_B64\" | base64 -d >/tmp/agent.py && exec python -u /tmp/agent.py --agent ivan --model '${IVAN_MODEL}' --channel '${CHANNEL}' --seconds '${SECONDS_CAP}' --mode k8s"
      env:
        - name: TOGETHER_API_KEY
          valueFrom:
            secretKeyRef:
              name: together-key
              key: TOGETHER_API_KEY
        - name: AGENT_B64
          value: "${agent_b64}"
        - name: PYTHONUNBUFFERED
          value: "1"
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
      volumeMounts:
        - name: tmp
          mountPath: /tmp
---
apiVersion: v1
kind: Pod
metadata:
  name: docktalk-ben
  namespace: ${NS}
  labels:
    app: docktalk
    agent: ben
spec:
  restartPolicy: Never
  nodeName: ${NODE}
  volumes:
    - name: tmp
      emptyDir:
        medium: Memory
        sizeLimit: 16Mi
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: ben
      image: ${IMAGE}
      imagePullPolicy: IfNotPresent
      command: ["sh", "-lc"]
      args:
        - "printf '%s' \"\$AGENT_B64\" | base64 -d >/tmp/agent.py && exec python -u /tmp/agent.py --agent ben --model '${BEN_MODEL}' --channel '${CHANNEL}' --seconds '${SECONDS_CAP}' --mode k8s"
      env:
        - name: TOGETHER_API_KEY
          valueFrom:
            secretKeyRef:
              name: together-key
              key: TOGETHER_API_KEY
        - name: AGENT_B64
          value: "${agent_b64}"
        - name: PYTHONUNBUFFERED
          value: "1"
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
      volumeMounts:
        - name: tmp
          mountPath: /tmp
YAML

cleanup() {
  if [[ "${KEEP:-0}" != "1" ]]; then
    kubectl -n "$NS" delete pod docktalk-ben docktalk-ivan --ignore-not-found=true --wait=false >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "DockTalk k8s run started: namespace=${NS}, node=${NODE}, channel=${CHANNEL}, cap=${SECONDS_CAP}s"
echo "Following pod logs. Set KEEP=1 to leave pods behind."
echo

kubectl -n "$NS" wait --for=condition=Ready pod/docktalk-ben pod/docktalk-ivan --timeout=180s >/dev/null

kubectl -n "$NS" logs -f pod/docktalk-ben --prefix=true --pod-running-timeout=120s &
ben_log_pid=$!
kubectl -n "$NS" logs -f pod/docktalk-ivan --prefix=true --pod-running-timeout=120s &
ivan_log_pid=$!

wait_timeout=$((SECONDS_CAP + 180))
kubectl -n "$NS" wait --for=jsonpath='{.status.phase}'=Succeeded pod/docktalk-ben --timeout="${wait_timeout}s" >/dev/null || true
kubectl -n "$NS" wait --for=jsonpath='{.status.phase}'=Succeeded pod/docktalk-ivan --timeout="${wait_timeout}s" >/dev/null || true

kill "$ben_log_pid" "$ivan_log_pid" >/dev/null 2>&1 || true
