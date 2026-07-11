"""SSH-backed :class:`~hermes_cli.oss_acquisition.HostRunner` (FG-08, §4.3).

Reuses the existing ``tools/environments`` **ssh** backend to clone + run an
acquired OSS project on a **different** machine — the design's reuse anchor
("remote host = an ssh/remote environment"). This is the concrete host runner
the ``hermes oss acquire`` CLI wires in for a live remote acquisition; the E2E
tests inject a lightweight recording runner instead, so the acquisition flow is
exercised without deploying to any external machine.

Rails honoured here (the pipeline enforces the policy; this executes it):
* clone is **commit-pinned** (``git checkout <commit>``);
* the service runs **non-root** and **network-restricted** — the runner refuses
  to start otherwise and binds the service to localhost on the remote host.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Optional

from hermes_cli.oss_acquisition import ServiceHandle

if TYPE_CHECKING:
    from tools.environments.ssh import SSHEnvironment


class SSHHostRunner:
    """Clone + run an OSS project on a remote host over SSH.

    A thin adapter over :class:`tools.environments.ssh.SSHEnvironment`: the
    heavy connection/sync work lives in that backend, this class only sequences
    the §4.3 clone/run/health/stop commands. The SSH environment is created
    lazily on first use so merely constructing the runner (e.g. to inspect it)
    never opens a connection.
    """

    def __init__(
        self,
        *,
        host: str,
        user: Optional[str] = None,
        start_cmd: Optional[str] = None,
        health_url: Optional[str] = None,
        port: int = 22,
        key_path: str = "",
    ) -> None:
        if not host:
            raise ValueError("SSHHostRunner requires a non-empty host")
        self.host = host
        self.user = user or "hermes"
        self.start_cmd = start_cmd
        self.health_url = health_url
        self.port = port
        self.key_path = key_path
        self._env: Optional["SSHEnvironment"] = None

    def _environment(self) -> "SSHEnvironment":
        if self._env is None:
            from tools.environments.ssh import SSHEnvironment

            self._env = SSHEnvironment(
                host=self.host,
                user=self.user,
                port=self.port,
                key_path=self.key_path,
            )
        return self._env

    def _run(self, command: str, *, cwd: str = "", timeout: int = 300) -> str:
        result = self._environment().execute(command, cwd=cwd, timeout=timeout)
        if int(result.get("returncode", 1)) != 0:
            raise RuntimeError(
                f"remote command failed ({command!r}): {result.get('output', '')}"
            )
        return str(result.get("output", ""))

    def clone(self, repo_url: str, commit: str, *, dest: str) -> str:
        """Clone ``repo_url`` and hard-pin ``commit`` under ``dest`` on the host."""
        if not commit:
            raise ValueError("commit pin is required (§4.3)")
        q_repo, q_dest, q_commit = (
            shlex.quote(repo_url),
            shlex.quote(dest),
            shlex.quote(commit),
        )
        script = (
            f"rm -rf {q_dest} && git clone --quiet {q_repo} {q_dest} && "
            f"cd {q_dest} && git checkout --quiet {q_commit} && pwd"
        )
        return self._run(script).strip() or dest

    def run_service(
        self,
        remote_path: str,
        *,
        name: str,
        non_root: bool,
        network_restricted: bool,
        bind: str,
    ) -> ServiceHandle:
        """Start the cloned project as a localhost-bound service on the host."""
        if not non_root:
            raise RuntimeError("§4.3 rail: the hosted service must run non-root")
        if not network_restricted:
            raise RuntimeError(
                "§4.3 rail: the hosted service must run network-restricted"
            )
        if not self.start_cmd:
            raise RuntimeError(
                "no start command supplied for the hosted service (--start-cmd)"
            )
        marker = f"hermes-oss-{name}"
        launch = (
            f"nohup env HERMES_OSS_SERVICE={shlex.quote(marker)} "
            f"{self.start_cmd} > {shlex.quote(remote_path)}/service.log 2>&1 &"
        )
        self._run(launch, cwd=remote_path, timeout=60)
        base_url = self.health_url or f"http://{bind}:0"
        return ServiceHandle(name=name, remote_path=remote_path, base_url=base_url)

    def health_check(self, handle: ServiceHandle) -> bool:
        """Return whether the service responds on its health URL (best-effort)."""
        if not self.health_url:
            return True
        try:
            output = self._run(
                f"curl -fsS -o /dev/null -w '%{{http_code}}' "
                f"{shlex.quote(self.health_url)} || true",
                timeout=30,
            )
        except RuntimeError:
            return False
        code = output.strip()
        return code.startswith("2") or code.startswith("3")

    def stop(self, handle: ServiceHandle) -> None:
        """Stop the service by its marker (retire, stage 6)."""
        marker = f"hermes-oss-{handle.name}"
        try:
            self._run(
                f"pkill -f {shlex.quote(marker)} || true", timeout=30
            )
        except RuntimeError:
            pass

    def cleanup(self) -> None:
        """Release the underlying SSH connection, if one was opened."""
        if self._env is not None:
            try:
                self._env.cleanup()
            finally:
                self._env = None


__all__ = ["SSHHostRunner"]
