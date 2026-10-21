FROM python:3.13-slim AS agent

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY workbench/server.py /app/server.py
COPY sample-project/ /opt/repo-workbench-seed/

ENV PORT=80 \
    WORKSPACE_DIR=/workspace/repository \
    SEED_DIR=/opt/repo-workbench-seed \
    ACTOR_INFO_DIR=/run/ate

EXPOSE 80

CMD ["python3", "/app/server.py"]

FROM python:3.13-slim AS source-control

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "PyJWT==2.10.1" "cryptography==44.0.1"

WORKDIR /app

COPY source-control/server.py /app/server.py

ENV PORT=80 \
    TOKEN_FILE=/run/source-control/token \
    ACCOUNT_NAME=repo-workbench-bot

EXPOSE 80

CMD ["python3", "/app/server.py"]

FROM python:3.13-slim AS webapp
ARG TARGETARCH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY webapp/server.py webapp/index.html webapp/kubeconfig.yaml /app/
COPY .bin/kubectl-ate-linux-${TARGETARCH} /usr/local/bin/kubectl-ate

ENV PORT=80 \
    AGENT_URL=http://atenet-router.ate-system.svc.cluster.local \
    ATE_CLI=/usr/local/bin/kubectl-ate \
    ATE_ENDPOINT=api.ate-system.svc:443 \
    ATE_KUBECONFIG=/app/kubeconfig.yaml \
    SOURCE_CONTROL_URL=http://source-control.book-workbench.svc.cluster.local \
    BOOK_ATESPACE=book-workbench \
    BOOK_TEMPLATE=repo-workbench \
    ACTOR_PREFIX=repo-workbench

EXPOSE 80

CMD ["python3", "/app/server.py"]
