from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .downstream import DownstreamMcpError, DownstreamTimeoutError, DownstreamToolError


class StdioMcpTransport:
    """JSON-RPC over STDIO client for downstream MCP servers.

    The subprocess receives only the explicit ``env`` mapping supplied to the
    constructor, after CoreMCP/admin/client authorization-like variables are
    removed. Parent process environment variables are intentionally never
    inherited.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        max_response_bytes: int = 1024 * 1024,
        idle_timeout_seconds: float | None = None,
    ) -> None:
        if not command:
            raise ValueError("stdio command must not be empty")
        if idle_timeout_seconds is not None and idle_timeout_seconds < 0:
            raise ValueError("idle_timeout_seconds must be non-negative")
        self.command = [str(part) for part in command]
        self.cwd = str(cwd) if cwd is not None else None
        self.env = self._sanitize_env(env or {})
        self.timeout = timeout
        self.max_response_bytes = max(1, max_response_bytes)
        self.idle_timeout_seconds = idle_timeout_seconds

        self.started_at: float | None = None
        self.last_used_at: float | None = None
        self.restart_count = 0
        self.last_exit_code: int | None = None
        self.last_error: str | None = None

        self._process: asyncio.subprocess.Process | None = None
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._stderr_tail = b""
        self._start_count = 0

    @property
    def is_running(self) -> bool:
        process = self._process
        if process is None or process.returncode is not None:
            return False
        if self._reader_task is not None and self._reader_task.done():
            return False
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "last_used_at": self.last_used_at,
            "restart_count": self.restart_count,
            "last_exit_code": self.last_exit_code,
            "last_error": self.last_error,
            "is_running": self.is_running,
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "pending_requests": len(self._pending),
            "stderr_tail": self._stderr_tail_text(),
        }

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        request_id: Any = 1,
        protocol_version: str | None = None,
        session_id: str | None = None,
        expect_response: bool = True,
        correlation_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        # STDIO MCP has no HTTP headers. These compatibility parameters are
        # accepted so callers can share the HTTP downstream client call shape.
        del protocol_version, session_id, correlation_id

        process = await self._ensure_started()
        if process.stdin is None:
            raise DownstreamMcpError("downstream stdio stdin is unavailable")
        self._touch()

        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params

        pending_key = self._id_key(request_id)
        future: asyncio.Future[dict[str, Any]] | None = None
        if expect_response:
            future = asyncio.get_running_loop().create_future()
            self._pending[pending_key] = future

        try:
            line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
            async with self._write_lock:
                try:
                    process.stdin.write(line)
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError) as exc:
                    if future is not None and self._pending.get(pending_key) is future:
                        self._pending.pop(pending_key, None)
                    await self._record_process_failure(
                        process,
                        f"downstream stdio write failed: {exc.__class__.__name__}",
                        fail_pending=True,
                    )
                    raise DownstreamMcpError("downstream stdio process closed") from exc

            if not expect_response:
                return {"jsonrpc": "2.0", "id": request_id, "result": {}}

            assert future is not None
            try:
                response = await asyncio.wait_for(future, timeout=timeout if timeout is not None else self.timeout)
            except TimeoutError as exc:
                if self._pending.get(pending_key) is future:
                    self._pending.pop(pending_key, None)
                raise DownstreamTimeoutError("downstream stdio request timed out", code=-32008) from exc
        except Exception:
            if future is not None and self._pending.get(pending_key) is future:
                self._pending.pop(pending_key, None)
            raise
        finally:
            self._touch()

        if "error" in response:
            error = response.get("error") or {}
            code = error.get("code", -32000) if isinstance(error, dict) else -32000
            message = error.get("message", "downstream error") if isinstance(error, dict) else "downstream error"
            if method == "tools/call":
                raise DownstreamToolError(str(message), code=int(code))
            raise DownstreamMcpError(str(message), code=int(code))
        return response

    def close(self) -> None:
        """Best-effort synchronous close helper.

        In an active event loop this schedules ``aclose``. Outside an event loop
        it runs the async close to completion.
        """

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        loop.create_task(self.aclose())

    async def aclose(self) -> None:
        process = self._process
        if process is None:
            return
        await self._stop_process(process, "downstream stdio transport closed", fail_pending=True)

    async def reap_idle(self, now: float | None = None) -> bool:
        if self.idle_timeout_seconds is None:
            return False
        process = self._process
        if process is None or not self.is_running or self._pending:
            return False

        last_used_at = self.last_used_at if self.last_used_at is not None else self.started_at
        if last_used_at is None:
            return False

        current = self._now() if now is None else now
        if current - last_used_at < self.idle_timeout_seconds:
            return False

        await self._stop_process(
            process,
            f"downstream stdio idle timeout after {self.idle_timeout_seconds:g}s",
            fail_pending=False,
        )
        return True

    async def maybe_reap_idle(self, now: float | None = None) -> bool:
        return await self.reap_idle(now=now)

    async def _ensure_started(self) -> asyncio.subprocess.Process:
        async with self._start_lock:
            if self._process is not None:
                if self.is_running:
                    return self._process
                await self._stop_process(
                    self._process,
                    "downstream stdio process closed before restart",
                    fail_pending=True,
                    natural_wait_seconds=0.1,
                )
                self._process = None

            try:
                self._process = await asyncio.create_subprocess_exec(
                    *self.command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.cwd,
                    env=self.env,
                )
            except OSError as exc:
                raise DownstreamMcpError(f"failed to start downstream stdio process: {exc}") from exc

            self._start_count += 1
            if self._start_count > 1:
                self.restart_count += 1
            now = self._now()
            self.started_at = now
            self.last_used_at = now
            self._reader_task = asyncio.create_task(self._read_stdout_loop())
            self._stderr_task = asyncio.create_task(self._drain_stderr_loop())
            return self._process

    async def _read_stdout_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        cancelled = False
        close_reason = "downstream stdio process closed"
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                if len(line) > self.max_response_bytes:
                    self._fail_pending(
                        DownstreamMcpError(
                            f"downstream stdio response exceeds {self.max_response_bytes} bytes",
                            code=-32009,
                        )
                    )
                    continue
                try:
                    data = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    self._fail_pending(DownstreamMcpError("downstream stdio returned non-JSON response"))
                    continue
                if not isinstance(data, dict):
                    self._fail_pending(DownstreamMcpError("downstream stdio returned invalid JSON-RPC response"))
                    continue
                response_id = data.get("id")
                future = self._pending.pop(self._id_key(response_id), None)
                if future is not None and not future.done():
                    future.set_result(data)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:  # pragma: no cover - defensive guard for reader task failures.
            close_reason = f"downstream stdio read failed: {exc}"
        finally:
            if self._reader_task is asyncio.current_task():
                self._reader_task = None
            if not cancelled:
                await self._record_process_failure(process, close_reason, fail_pending=True)

    async def _drain_stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                chunk = await process.stderr.read(4096)
                if not chunk:
                    break
                self._stderr_tail = (self._stderr_tail + chunk)[-8192:]
        except asyncio.CancelledError:
            raise
        finally:
            if self._stderr_task is asyncio.current_task():
                self._stderr_task = None

    def _fail_pending(self, exc: Exception) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(exc)

    async def _record_process_failure(
        self,
        process: asyncio.subprocess.Process,
        reason: str,
        *,
        fail_pending: bool,
        natural_wait_seconds: float = 0.2,
    ) -> None:
        if self._process is process:
            self._process = None
        exit_code = await self._wait_for_exit_or_terminate(
            process,
            natural_wait_seconds=natural_wait_seconds,
        )
        message = self._format_exit_message(reason, exit_code)
        self._record_process_stopped(process, message, exit_code)
        await self._settle_stderr_task()
        if fail_pending:
            self._fail_pending(DownstreamMcpError(message))

    async def _stop_process(
        self,
        process: asyncio.subprocess.Process,
        reason: str,
        *,
        fail_pending: bool,
        natural_wait_seconds: float = 0.0,
    ) -> None:
        if self._process is process:
            self._process = None
        if fail_pending:
            self._fail_pending(DownstreamMcpError(reason))
        await self._cancel_io_tasks()
        exit_code = await self._wait_for_exit_or_terminate(
            process,
            natural_wait_seconds=natural_wait_seconds,
        )
        message = self._format_exit_message(reason, exit_code)
        self._record_process_stopped(process, message, exit_code)

    async def _wait_for_exit_or_terminate(
        self,
        process: asyncio.subprocess.Process,
        *,
        natural_wait_seconds: float,
        terminate_grace_seconds: float = 2.0,
    ) -> int | None:
        if process.returncode is None and natural_wait_seconds > 0:
            try:
                await asyncio.wait_for(process.wait(), timeout=natural_wait_seconds)
            except TimeoutError:
                pass

        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:  # pragma: no cover - process exited between checks.
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=terminate_grace_seconds)
            except TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:  # pragma: no cover - process exited between checks.
                    pass
                await process.wait()

        return process.returncode

    async def _cancel_io_tasks(self) -> None:
        current_task = asyncio.current_task()
        tasks = [
            task
            for task in (self._reader_task, self._stderr_task)
            if task is not None and task is not current_task
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._reader_task in tasks:
            self._reader_task = None
        if self._stderr_task in tasks:
            self._stderr_task = None

    async def _settle_stderr_task(self, timeout: float = 0.2) -> None:
        task = self._stderr_task
        if task is None or task is asyncio.current_task():
            return
        if not task.done():
            done, _ = await asyncio.wait({task}, timeout=timeout)
            if not done:
                task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if self._stderr_task is task:
            self._stderr_task = None

    def _record_process_stopped(
        self,
        process: asyncio.subprocess.Process,
        message: str,
        exit_code: int | None,
    ) -> None:
        if self._process is process:
            self._process = None
        self.last_exit_code = exit_code
        self.last_error = self._safe_error_text(message)

    def _touch(self) -> None:
        self.last_used_at = self._now()

    def _stderr_tail_text(self) -> str:
        return self._stderr_tail.decode("utf-8", errors="replace")

    @staticmethod
    def _format_exit_message(reason: str, exit_code: int | None) -> str:
        if exit_code is None:
            return reason
        return f"{reason} with exit code {exit_code}"

    @staticmethod
    def _safe_error_text(message: str) -> str:
        return str(message)[:2048]

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _id_key(request_id: Any) -> str:
        try:
            return json.dumps(request_id, sort_keys=True, separators=(",", ":"))
        except TypeError:
            return repr(request_id)

    @classmethod
    def _sanitize_env(cls, env: Mapping[str, str]) -> dict[str, str]:
        sanitized: dict[str, str] = {}
        for key, value in env.items():
            key_str = str(key)
            if cls._is_forbidden_env_key(key_str):
                continue
            sanitized[key_str] = str(value)
        return sanitized

    @staticmethod
    def _is_forbidden_env_key(key: str) -> bool:
        normalized = key.replace("-", "_").upper()
        if "AUTHORIZATION" in normalized:
            return True
        return normalized.startswith("COREMCP_ADMIN_TOKEN") or normalized.startswith("COREMCP_CLIENT_TOKEN")


StdioMcpClient = StdioMcpTransport
