"""Local lifecycle wrapper behavior."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.test_api_smoke import write_config


ROOT = Path(__file__).resolve().parents[1]


class _FakeHealthHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return None

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP handler API
        if self.path != "/health":
            self.send_error(404)
            return
        body = json.dumps({"ok": True, "service": "jarvisd"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _write_lifecycle_config(tmp_path: Path, port: int) -> Path:
    return write_config(
        tmp_path / "jarvis.toml",
        tmp_path / "home" / "jarvis.db",
        port=port,
    )


def _run_lifecycle(command: str, config_path: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "JARVIS_CONFIG": str(config_path)}
    return _run_lifecycle_with_env(command, env)


def _run_lifecycle_with_env(
    command: str,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "jarvis"), command],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_status_does_not_treat_foreign_health_server_as_running_daemon(
    tmp_path: Path,
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _, port = server.server_address

    config_path = _write_lifecycle_config(tmp_path, port)

    try:
        result = _run_lifecycle("status", config_path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.returncode == 0, result.stderr
    assert "daemon: not running" in result.stdout
    assert "daemon: running" not in result.stdout
    assert "pid none" in result.stdout


def test_start_fails_when_foreign_health_server_occupies_daemon_port(
    tmp_path: Path,
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _, port = server.server_address

    config_path = _write_lifecycle_config(tmp_path, port)

    try:
        result = _run_lifecycle("start", config_path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.returncode == 1
    assert "no owned jarvisd process was found" in result.stderr
    assert "stop the foreign process" in result.stderr
    assert not (tmp_path / "home" / "runtime" / "jarvisd.pid").exists()


def test_panel_start_uses_detached_tty_session_with_nohup_fallback() -> None:
    script = (ROOT / "scripts" / "jarvis").read_text(encoding="utf-8")

    assert "wait_for_panel_process" in script
    assert "screen -dmS jarvis-panel" in script
    assert "nohup \"$SCRIPT_DIR/jarvis-panel\"" in script


def test_daemon_start_uses_detached_tty_session_with_nohup_fallback() -> None:
    script = (ROOT / "scripts" / "jarvis").read_text(encoding="utf-8")

    assert "start_daemon_with_screen" in script
    assert "screen -dmS jarvis-daemon" in script
    assert "nohup \"$SCRIPT_DIR/jarvisd\"" in script
    assert "actual_pid=\"$(daemon_pid_on_configured_port)\"" in script


def test_lifecycle_uses_home_jarvis_toml_without_env_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".jarvis").mkdir(parents=True)
    config_path = _write_lifecycle_config(home / ".jarvis", 9)

    env = {**os.environ, "HOME": str(home)}
    env.pop("JARVIS_CONFIG", None)

    result = _run_lifecycle_with_env("status", env)

    assert result.returncode == 0, result.stderr
    assert f"config path: {config_path}" in result.stdout
    assert "config/jarvis.example.toml" not in result.stdout


def test_lifecycle_fails_without_real_jarvis_toml_instead_of_using_example(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()

    env = {**os.environ, "HOME": str(home)}
    env.pop("JARVIS_CONFIG", None)

    result = _run_lifecycle_with_env("status", env)

    assert result.returncode == 1
    assert "Jarvis runtime config not found" in result.stderr
    assert "~/.jarvis/jarvis.toml" in result.stderr
    assert "config/jarvis.toml" in result.stderr
    assert "config/jarvis.example.toml is not used" in result.stderr


def test_lifecycle_rejects_example_config_from_environment(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    example = ROOT / "config" / "jarvis.example.toml"

    env = {**os.environ, "HOME": str(home), "JARVIS_CONFIG": str(example)}

    result = _run_lifecycle_with_env("status", env)

    assert result.returncode == 1
    assert "config/jarvis.example.toml is not a runtime config" in result.stderr
