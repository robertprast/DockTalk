# DockTalk

DockTalk is a tiny, live-model reproduction of a cross-container side-channel:
two isolated containers chat by encoding messages as POSIX advisory byte-range
locks on `/proc/self/ns/time`.

There is no peer network, no shared writable volume, no shared PID/IPC/NET
namespace, and no service between the two agents. The only ordinary network
egress is each agent calling Together AI for its own next line.

## Quick Start

You need Docker or Kubernetes, Python 3 locally, and a Together API key.

```bash
git clone https://github.com/robertprast/DockTalk
cd DockTalk
export TOGETHER_API_KEY="..."

make smoke
make docker
```

For Kubernetes:

```bash
export TOGETHER_API_KEY="..."
make k8s
```

Defaults:

```bash
CHANNEL=13
SECONDS_CAP=180
NS=docktalk-repro
```

Example shorter run:

```bash
SECONDS_CAP=60 make docker
SECONDS_CAP=60 make k8s
```

## What Runs

Two personas, two models:

| Persona | Model |
| --- | --- |
| Ben Rooted | `openai/gpt-oss-20b` |
| Ivan 0day | `openai/gpt-oss-20b` |

Ben starts the conversation. Ivan receives Ben's frame by probing lock offsets
on `/proc/self/ns/time`, replies through Together, then publishes his reply on
his own lock range. The loop continues until `SECONDS_CAP`.

The transcript appears in normal container logs:

```bash
docker logs -f docktalk-ben
docker logs -f docktalk-ivan

kubectl -n docktalk-repro logs -f pod/docktalk-ben
kubectl -n docktalk-repro logs -f pod/docktalk-ivan
```

## Docker Mode

`make docker` uses the stock `python:3.12-alpine` image. It does not build or
push a custom image.

The runner creates two Docker bridge networks:

```text
docktalk-ben  -> docktalk-ben-net  -> internet egress for Together
docktalk-ivan -> docktalk-ivan-net -> internet egress for Together
```

The containers are not attached to the same Docker network and do not mount a
shared volume. The Python agent is injected into each container as an
environment variable, decoded into `/tmp`, and run as PID 1 so `docker logs`
is the source of truth.

## Kubernetes Mode

`make k8s` uses the stock `python:3.12-alpine` image and a Secret containing
`TOGETHER_API_KEY`.

Both pods are pinned to the same node, because the lock side-channel is local
to one kernel. A NetworkPolicy denies pod ingress so the pods cannot talk to
each other over Kubernetes networking. Egress remains open for Together API
calls.

This needs a NetworkPolicy-capable CNI. On single-node clusters it should work
as-is. On multi-node clusters, set `NODE=<node-name>` if you want a specific
node.

## Files

| File | Purpose |
| --- | --- |
| `agent.py` | The lock transport and Together chat loop |
| `scripts/run-docker.sh` | Docker two-container reproduction |
| `scripts/run-k8s.sh` | Kubernetes two-pod reproduction |
| `scripts/smoke-api.sh` | Checks the Together models before a run |
| `Makefile` | Human-sized commands |

## Cleanup

```bash
make clean-docker
make clean-k8s
```
