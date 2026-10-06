# Semaphore UI image for the DGX Spark lab (Step 04 §4).
# The stock semaphoreui/semaphore image runs Ansible but has no Kubernetes
# tooling. The lab's playbooks 05/06/06b/13/21/99 run kubectl, helm and the
# kubernetes.core modules ON THE CONTROLLER — here, inside this container.
#
# Built on sema01 by docker compose (docker-compose.override.yml), with the
# repository's "01-Ansible/lab" folder as build context:
#   docker compose build semaphore && docker compose up -d
ARG SEMAPHORE_IMAGE=semaphoreui/semaphore:latest
# hadolint ignore=DL3006
FROM ${SEMAPHORE_IMAGE}

# Pin these to the cluster you run (02-Kubernetes/lab/versions.env).
ARG KUBECTL_VERSION=v1.36.5
ARG HELM_VERSION=v3.18.3
ARG TARGETARCH

USER root
SHELL ["/bin/sh", "-eu", "-c"]

# OS tools the playbooks call on the controller (ssh-keygen for play 1, git, jq, rsync).
RUN if command -v apk >/dev/null; then \
      apk add --no-cache bash curl git jq openssh-client rsync; \
    else \
      apt-get update && apt-get install -y --no-install-recommends bash curl git jq openssh-client rsync \
      && rm -rf /var/lib/apt/lists/*; \
    fi

# kubectl + helm for the controller-side Kubernetes tasks.
RUN arch="${TARGETARCH:-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/')}"; \
    curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${arch}/kubectl"; \
    chmod 0755 /usr/local/bin/kubectl; \
    curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-${arch}.tar.gz" | tar -xz -C /tmp; \
    mv "/tmp/linux-${arch}/helm" /usr/local/bin/helm; rm -rf "/tmp/linux-${arch}"

# Python libraries + Ansible collections, installed into the SAME Python that
# Semaphore's ansible-playbook uses (found from its shebang line).
COPY semaphore/requirements-semaphore.txt requirements.yml /tmp/spark-lab/
RUN py="$(head -1 "$(command -v ansible-playbook)" | sed 's/^#!//; s/^\/usr\/bin\/env //')"; \
    "$py" -m pip --version >/dev/null 2>&1 || "$py" -m ensurepip; \
    "$py" -m pip install --no-cache-dir -r /tmp/spark-lab/requirements-semaphore.txt \
      || "$py" -m pip install --no-cache-dir --break-system-packages -r /tmp/spark-lab/requirements-semaphore.txt; \
    ansible-galaxy collection install -r /tmp/spark-lab/requirements.yml -p /usr/share/ansible/collections; \
    rm -rf /tmp/spark-lab

# Persistent lab state (kubeconfigs, join files, reports): a volume mounted here.
RUN mkdir -p /var/lib/spark-lab/cache && chown -R 1001:0 /var/lib/spark-lab && chmod 0700 /var/lib/spark-lab/cache

# Back to Semaphore's own unprivileged user.
USER 1001
