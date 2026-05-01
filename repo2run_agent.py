"""Faithful Repo2Run agent for Harbor.

Replicates the iteration loop from Repo2Run's `build_agent/agents/configuration.py`:
- system message = the (Repo2Run) prompt (= the task's `instruction.md`)
- user message #1 = `[Project root Path]: /repo`
- per-turn output: `### Thought:` text + ONE ```bash ... ``` block
- per-turn observation: `### Observation:\n<command stdout/stderr>`
- success: `runtest` or `poetryruntest` returns rc 0 or 5

LLM calls happen on the harbor host (so internal proxies like
`litellm-external.labs.jb.gg/v1` work). The agent maintains its own
`cwd` + exported-env across env.exec() calls so `cd` / `export` look
persistent to the model — matching Repo2Run's pexpect-shell semantics.

Register via:
    harbor run --agent-import-path /path/to/repo2run_agent.py:Repo2RunAgent ...
"""

from __future__ import annotations

import re
import shlex
import time
from pathlib import Path
from typing import Any

import litellm

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.agent.context import AgentContext


_BASH_BLOCK = re.compile(r"```bash\s*\n(.*?)\n```", re.DOTALL)
_CWD_MARKER = "__REPO2RUN_CWD__="
_ENV_BEGIN = "__REPO2RUN_ENV_BEGIN__"
_ENV_END = "__REPO2RUN_ENV_END__"

# Tools we drop into the sandbox so the upstream Repo2Run prompt's
# `runtest` / `poetryruntest` commands work verbatim.
_RUNTEST_SH = r"""#!/bin/bash
# /usr/local/bin/runtest — port of build_agent/tools/runtest.py
set -uo pipefail
cd /repo
pytest --collect-only -q --disable-warnings >/tmp/_runtest.log 2>&1
RC=$?
cat /tmp/_runtest.log
case $RC in
  0) echo; echo "Congratulations, you have successfully configured the environment!"; exit 0 ;;
  5) echo; echo "No unit tests were detected in this repository, so it passes. Congratulations, you have successfully configured the environment!"; exit 5 ;;
  *) echo; echo "Error: Please modify the configuration according to the error messages above. Once all issues are resolved, rerun the tests."; exit $RC ;;
esac
"""

_POETRYRUNTEST_SH = r"""#!/bin/bash
# /usr/local/bin/poetryruntest — port of build_agent/tools/poetryruntest.py
set -uo pipefail
cd /repo
poetry run pytest --collect-only -q --disable-warnings >/tmp/_poetry_runtest.log 2>&1
RC=$?
cat /tmp/_poetry_runtest.log
case $RC in
  0) echo; echo "Congratulations, you have successfully configured the environment!"; exit 0 ;;
  5) echo; echo "No unit tests were detected in this repository, so it passes. Congratulations, you have successfully configured the environment!"; exit 5 ;;
  *) echo; echo "Error: Please modify the configuration according to the error messages above. Once all issues are resolved, rerun the tests."; exit $RC ;;
esac
"""


class Repo2RunAgent(BaseAgent):
    """Repo2Run-style agent: ```bash``` blocks, persistent shell, runtest helper."""

    SUPPORTS_ATIF: bool = False  # we emit our own simple transcript

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        max_turns: int = 100,
        api_base: str | None = None,
        temperature: float = 0.7,
        per_command_timeout_sec: int = 600,
        truncate_chars: int = 12000,
        llm_call_kwargs: dict[str, Any] | None = None,
        **kwargs,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self._max_turns = int(max_turns)
        self._api_base = api_base
        self._temperature = float(temperature)
        self._per_cmd_timeout = int(per_command_timeout_sec)
        self._truncate = int(truncate_chars)
        self._llm_call_kwargs = dict(llm_call_kwargs or {})

        # Persistent shell state, mirroring Repo2Run's pexpect session.
        self._cwd: str = "/"
        self._env: dict[str, str] = {}

        # Transcript files for inspection.
        self._transcript_path = self.logs_dir / "repo2run-agent.txt"
        self._transcript_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def name() -> str:
        return "repo2run-agent"

    def version(self) -> str:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        # Drop the runtest / poetryruntest helpers into the sandbox.
        for path, body in (
            ("/usr/local/bin/runtest", _RUNTEST_SH),
            ("/usr/local/bin/poetryruntest", _POETRYRUNTEST_SH),
        ):
            here = f"cat > {path} <<'__R2R_EOF__'\n{body}__R2R_EOF__\nchmod +x {path}"
            await environment.exec(command=here, user="root")

    # ------------------------------------------------------------------
    #  shell-state machinery: each model command runs in a fresh subshell
    #  but we re-apply cwd/exports so cd/export look persistent.
    # ------------------------------------------------------------------
    async def _exec_with_state(
        self, environment: BaseEnvironment, model_cmd: str
    ) -> ExecResult:
        env_exports = "\n".join(
            f"export {k}={shlex.quote(v)}" for k, v in self._env.items()
        )
        # e2b's commands.run() doesn't inherit the Dockerfile's ENV PATH in
        # full (gives a minimal /usr/local/sbin:/usr/local/bin:... default).
        # Repo2Run upstream sidesteps this with an interactive pexpect bash
        # that sources /etc/profile. We're non-interactive, so re-prepend
        # the tool dirs the env Dockerfile installed into.
        wrapper = f"""set +e
export PATH="/root/.local/bin:/usr/local/bin:/usr/bin:/bin:${{PATH:-}}"
cd {shlex.quote(self._cwd)} 2>/dev/null || cd /
{env_exports}
{model_cmd}
__R2R_RC=$?
echo
echo {_CWD_MARKER}$(pwd)
echo {_ENV_BEGIN}
declare -px 2>/dev/null
echo {_ENV_END}
exit $__R2R_RC
"""
        result = await environment.exec(
            command=wrapper, timeout_sec=self._per_cmd_timeout, user="root"
        )

        # Strip the trailing markers from stdout, and update agent state.
        stdout = result.stdout or ""
        new_cwd, new_env, visible = self._parse_state_markers(stdout)
        if new_cwd is not None:
            self._cwd = new_cwd
        if new_env is not None:
            # Skip vars that change every shell (BASH*, PWD, OLDPWD, _, SHLVL...)
            # and that already match the inherited env.
            stable = self._filter_env(new_env)
            self._env = {**self._env, **stable}

        result.stdout = visible
        return result

    @staticmethod
    def _parse_state_markers(stdout: str) -> tuple[str | None, dict | None, str]:
        cwd = None
        env: dict[str, str] | None = None
        out_lines: list[str] = []
        in_env = False
        env_lines: list[str] = []
        for line in stdout.splitlines():
            if line.startswith(_CWD_MARKER):
                cwd = line[len(_CWD_MARKER):].strip()
                continue
            if line == _ENV_BEGIN:
                in_env = True
                continue
            if line == _ENV_END:
                in_env = False
                continue
            if in_env:
                env_lines.append(line)
            else:
                out_lines.append(line)
        if env_lines:
            env = {}
            # `declare -x KEY="value"` per line
            decl_re = re.compile(r'^declare -x\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$')
            for ln in env_lines:
                m = decl_re.match(ln)
                if not m:
                    continue
                k, v = m.group(1), m.group(2)
                # Strip surrounding double-quotes if present.
                if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
                    v = v[1:-1].replace('\\"', '"').replace('\\$', '$').replace('\\`', '`')
                env[k] = v
        return cwd, env, "\n".join(out_lines)

    @staticmethod
    def _filter_env(env: dict[str, str]) -> dict[str, str]:
        skip_prefixes = ("BASH", "_", "SHLVL", "PWD", "OLDPWD", "PS1", "PS2", "PS4")
        skip_exact = {"SHLVL", "_", "PWD", "OLDPWD", "TERM", "SHELL", "HOME", "USER",
                      "LOGNAME", "PATH"}  # PATH is set by Dockerfile; don't snapshot it
        out: dict[str, str] = {}
        for k, v in env.items():
            if k in skip_exact:
                continue
            if any(k.startswith(p) for p in skip_prefixes):
                continue
            out[k] = v
        return out

    # ------------------------------------------------------------------
    def _truncate_obs(self, text: str) -> str:
        if len(text) <= self._truncate * 2:
            return text
        head = text[: self._truncate]
        tail = text[-self._truncate:]
        return f"{head}\n...[truncated {len(text) - 2*self._truncate} chars]...\n{tail}"

    @staticmethod
    def _extract_bash(text: str) -> str | None:
        m = _BASH_BLOCK.search(text)
        return m.group(1).strip() if m else None

    def _log(self, *parts: str) -> None:
        with self._transcript_path.open("a") as f:
            f.write("\n".join(parts) + "\n")

    # ------------------------------------------------------------------
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # Repo2Run's framing: instruction = system; first user msg = the cd line.
        messages: list[dict[str, str]] = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": "[Project root Path]: /repo"},
        ]
        self._cwd = "/"
        self._env = {}

        n_in = n_out = n_cache = 0
        cost = 0.0

        self._log(f"=== Repo2RunAgent | model={self.model_name} | "
                  f"max_turns={self._max_turns} | api_base={self._api_base or '(default)'} ===")

        for turn in range(1, self._max_turns + 1):
            self._log(f"\n----- turn {turn} -----")
            try:
                t0 = time.time()
                resp = await litellm.acompletion(
                    model=self.model_name,
                    messages=messages,
                    api_base=self._api_base,
                    temperature=self._temperature,
                    **self._llm_call_kwargs,
                )
                dt = time.time() - t0
            except Exception as e:
                self._log(f"!! LLM error: {e}")
                break

            choice = resp.choices[0].message
            assistant_text = choice.content or ""
            messages.append({"role": "assistant", "content": assistant_text})

            usage = getattr(resp, "usage", None)
            if usage is not None:
                n_in += int(getattr(usage, "prompt_tokens", 0) or 0)
                n_out += int(getattr(usage, "completion_tokens", 0) or 0)
                pt = getattr(usage, "prompt_tokens_details", None)
                if pt is not None:
                    n_cache += int(getattr(pt, "cached_tokens", 0) or 0)

            self._log(f"assistant ({dt:.1f}s):\n{assistant_text}")

            cmd = self._extract_bash(assistant_text)
            if cmd is None:
                obs = (
                    "ERROR! Your reply does not contain valid block or final answer. "
                    "Please respond with a single ```bash ... ``` block."
                )
                self._log(f"observation:\n{obs}")
                messages.append({"role": "user", "content": f"### Observation:\n{obs}"})
                continue

            self._log(f"command:\n{cmd}")

            result = await self._exec_with_state(environment, cmd)
            stdout = self._truncate_obs(result.stdout or "")
            stderr = self._truncate_obs(result.stderr or "")
            obs_body = stdout
            if stderr.strip():
                obs_body += f"\n[stderr]\n{stderr}"

            self._log(f"observation (rc={result.return_code}):\n{obs_body}")

            # `runtest` / `poetryruntest` success -> stop.
            if result.return_code in (0, 5) and (
                "Congratulations, you have successfully configured the environment!" in stdout
            ):
                self._log("=== success: runtest passed ===")
                messages.append({"role": "user", "content": f"### Observation:\n{obs_body}"})
                break

            messages.append({"role": "user", "content": f"### Observation:\n{obs_body}"})

        context.n_input_tokens = n_in or None
        context.n_output_tokens = n_out or None
        context.n_cache_tokens = n_cache or None
        # Token-based cost estimation via litellm (best-effort).
        try:
            cost = float(litellm.completion_cost(
                model=self.model_name, prompt_tokens=n_in, completion_tokens=n_out
            ) or 0.0)
        except Exception:
            cost = 0.0
        context.cost_usd = cost or None
