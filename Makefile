IMAGE ?= python:3.12-alpine
CHANNEL ?= 13
SECONDS_CAP ?= 180
NS ?= docktalk-repro
NODE ?=

export IMAGE CHANNEL SECONDS_CAP NS NODE

.PHONY: smoke docker k8s clean-docker clean-k8s lint

smoke:
	./scripts/smoke-api.sh

docker:
	./scripts/run-docker.sh

k8s:
	./scripts/run-k8s.sh

clean-docker:
	-docker rm -f docktalk-ben docktalk-ivan >/dev/null 2>&1
	-docker network rm docktalk-ben-net docktalk-ivan-net >/dev/null 2>&1

clean-k8s:
	kubectl delete ns $(NS) --ignore-not-found=true

lint:
	python3 -m py_compile agent.py
	bash -n scripts/*.sh
