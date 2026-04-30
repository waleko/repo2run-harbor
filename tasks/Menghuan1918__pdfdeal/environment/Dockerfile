FROM python:3.10

ENV DEBIAN_FRONTEND=noninteractive

RUN mkdir -p ~/.pip && touch ~/.pip/pip.conf \
 && echo "[global]" >> ~/.pip/pip.conf \
 && echo "[install]" >> ~/.pip/pip.conf

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"

RUN pip install --no-cache-dir pytest pytest-xdist pipdeptree

# Auto-snapshot exported env after every interactive bash command so the
# verifier can replay vars the agent set with `export`. Matches Repo2Run's
# pexpect-shell semantics where agent exports persist into the test phase.
RUN printf '%s\n' \
    'PROMPT_COMMAND='"'"'declare -px > /tmp/repo2run-env-snapshot 2>/dev/null'"'"'${PROMPT_COMMAND:+;$PROMPT_COMMAND}' \
    'export PROMPT_COMMAND' \
    > /etc/profile.d/repo2run-env-snapshot.sh \
 && cat /etc/profile.d/repo2run-env-snapshot.sh >> /etc/bash.bashrc

RUN git config --global --add safe.directory /repo \
 && git clone https://github.com/Menghuan1918/pdfdeal.git /repo \
 && cd /repo && git checkout e081998afbc26c51f175e09882075c8e82316dc7

WORKDIR /app
