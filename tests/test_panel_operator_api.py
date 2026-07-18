"""Task 10: panel operator controls are thin API intents only.

Two layers under test:

* the cockpit JS model (``dan/panel/assets/app.js``, executed via node in a
  vm sandbox, same idiom as ``test_panel_assets.py``): every operator control
  maps to exactly one daemon route, an offline daemon blocks mutations, the
  voice stage labels distinguish accepted/synthesizing/played, and the
  "DAN padł"/"DAN znów działa" notifications fire exactly once per edge;
* the daemon endpoints backing those intents: ``POST
  /voice/queue/current/cancel`` (skip the claimed row, 404 when nothing is
  playing) and ``GET /sessions`` (truthful session/model usage — unknown is
  reported as "unknown", utterance text never leaves the daemon).
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import textwrap
from pathlib import Path

from dan.daemon.app import DaemonApp

from tests.test_api_smoke import request_json, running_server
from tests.test_voice_api_contract import SPEAK_TEXT, make_voice_app, speak_payload

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "dan" / "panel" / "assets" / "app.js"
INDEX_HTML = ROOT / "dan" / "panel" / "assets" / "index.html"


# --- JS harness --------------------------------------------------------------


def _run_harness(tmp_path: Path, name: str, body: str) -> None:
    harness = tmp_path / name
    harness.write_text(
        textwrap.dedent(
            f"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            const context = {{
              console,
              document: {{ addEventListener: () => {{}} }},
              window: {{}},
            }};
            context.globalThis = context;
            vm.runInNewContext(fs.readFileSync({str(APP_JS)!r}, "utf8"), context, {{
              filename: "app.js",
            }});

            (async () => {{
            {body}
            }})().catch((error) => {{
              console.error(error);
              process.exit(1);
            }});
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(harness)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_panel_controls_are_api_intents(tmp_path: Path) -> None:
    _run_harness(
        tmp_path,
        "operator-intents-harness.js",
        """
        const calls = [];
        const request = async (method, path) => {
          calls.push([method, path]);
          return { ok: true };
        };

        await context.sendOperatorIntent("pause_voice", { online: true, request });
        await context.sendOperatorIntent("skip_current", { online: true, request });
        await context.sendOperatorIntent("safe_restart", { online: true, request });

        assert.deepStrictEqual(calls, [
          ["POST", "/voice/pause"],
          ["POST", "/voice/queue/current/cancel"],
          ["POST", "/runtime/restart"],
        ]);

        const resume = await context.sendOperatorIntent("resume_voice", { online: true, request });
        assert.strictEqual(resume.ok, true);
        assert.deepStrictEqual(calls[3], ["POST", "/voice/resume"]);
        """,
    )


def test_offline_daemon_blocks_operator_mutations(tmp_path: Path) -> None:
    _run_harness(
        tmp_path,
        "operator-offline-harness.js",
        """
        const calls = [];
        const request = async (method, path) => {
          calls.push([method, path]);
          return {};
        };

        for (const name of ["pause_voice", "resume_voice", "skip_current", "safe_restart"]) {
          const result = await context.sendOperatorIntent(name, { online: false, request });
          assert.strictEqual(result.blocked, true, name);
          assert.match(result.message, /offline/i, name);
        }

        // No mutation may leave the panel while the daemon is down, and the
        // panel never tries to resurrect dand itself.
        assert.deepStrictEqual(calls, []);
        """,
    )


def test_panel_distinguishes_accepted_synthesized_and_played(tmp_path: Path) -> None:
    _run_harness(
        tmp_path,
        "voice-stage-label-harness.js",
        """
        const label = context.voiceStageLabel;

        assert.strictEqual(label({ status: "queued", playback_confirmed: false }), "przyjęto");
        assert.strictEqual(label({ status: "synthesizing", playback_confirmed: false }), "syntetyzowanie");
        // Synthesis done but playback telemetry not confirmed => still not "played".
        assert.strictEqual(label({ status: "done", playback_confirmed: false }), "syntetyzowanie");
        assert.strictEqual(label({ status: "done", playback_confirmed: true }), "odtworzono");
        assert.strictEqual(label({ status: "cancelled", playback_confirmed: false }), "anulowano");
        assert.strictEqual(label({ status: "failed", playback_confirmed: false }), "błąd");
        """,
    )


def test_down_and_recovered_notify_once(tmp_path: Path) -> None:
    _run_harness(
        tmp_path,
        "daemon-availability-harness.js",
        """
        const messages = [];
        const tracker = context.createDaemonAvailabilityTracker((message) => messages.push(message));

        tracker.poll(false);
        tracker.poll(false);
        tracker.poll(true);
        tracker.poll(true);
        assert.deepStrictEqual(messages, ["DAN padł", "DAN znów działa"]);

        tracker.poll(false);
        tracker.poll(false);
        assert.deepStrictEqual(messages, ["DAN padł", "DAN znów działa", "DAN padł"]);

        // A healthy first observation is not news.
        const quiet = [];
        const freshTracker = context.createDaemonAvailabilityTracker((message) => quiet.push(message));
        freshTracker.poll(true);
        assert.deepStrictEqual(quiet, []);
        """,
    )


def test_operator_buttons_are_wired_and_offline_gated() -> None:
    script = APP_JS.read_text(encoding="utf-8")
    markup = INDEX_HTML.read_text(encoding="utf-8")

    for element_id in (
        "pauseVoiceButton",
        "resumeVoiceButton",
        "cancelCurrentSpeechButton",
        "restartDANButton",
    ):
        assert f'id="{element_id}"' in markup, element_id

    for intent in ("pause_voice", "resume_voice", "skip_current", "safe_restart"):
        assert f'"{intent}"' in script, intent

    # The offline gate must actually disable the operator buttons.
    interactive = re.search(
        r"function setInteractiveEnabled\(enabled\) \{.*?\n\}", script, re.DOTALL
    )
    assert interactive is not None
    for element_id in (
        "pauseVoiceButton",
        "resumeVoiceButton",
        "cancelCurrentSpeechButton",
        "restartDANButton",
    ):
        assert f"el.{element_id}" in interactive.group(0), element_id

    # The dead "not implemented" placeholder is gone for good.
    assert "not implemented" not in script.lower()


# --- POST /voice/queue/current/cancel ---------------------------------------


def _mark_status(app: DaemonApp, request_id: str, status: str) -> None:
    """Walk the row through the legal transition chain up to ``status``."""

    chain = ["synthesizing", "speaking"]
    steps = chain[: chain.index(status) + 1] if status in chain else [status]
    conn = sqlite3.connect(app.config.database.path)
    try:
        for step in steps:
            with conn:
                conn.execute(
                    "UPDATE voice_queue SET status = ? WHERE id = ?",
                    (step, request_id),
                )
    finally:
        conn.close()


def _queue_status(app: DaemonApp, request_id: str) -> str:
    conn = sqlite3.connect(app.config.database.path)
    try:
        row = conn.execute(
            "SELECT status FROM voice_queue WHERE id = ?", (request_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return str(row[0])


def test_current_cancel_cancels_the_claimed_row_only(tmp_path: Path) -> None:
    app = make_voice_app(tmp_path)
    try:
        with running_server(app) as base_url:
            _, speaking = request_json(
                "POST", f"{base_url}/voice/speak", speak_payload(session="a")
            )
            _, waiting = request_json(
                "POST", f"{base_url}/voice/speak", speak_payload(session="b")
            )
            _mark_status(app, speaking["request_id"], "speaking")

            status, body = request_json(
                "POST", f"{base_url}/voice/queue/current/cancel", {}
            )

            assert status == 200
            assert body["ok"] is True
            assert body["request_id"] == speaking["request_id"]
            assert body["status"] == "cancelled"
            # Skip means skip: the rest of the queue survives.
            assert _queue_status(app, speaking["request_id"]) == "cancelled"
            assert _queue_status(app, waiting["request_id"]) == "queued"
    finally:
        app.close()


def test_current_cancel_is_404_when_nothing_is_playing(tmp_path: Path) -> None:
    app = make_voice_app(tmp_path)
    try:
        with running_server(app) as base_url:
            # Empty queue: nothing claimed at all.
            status, _body = request_json(
                "POST", f"{base_url}/voice/queue/current/cancel", {}
            )
            assert status == 404

            # A merely queued (unclaimed) row is not "currently playing" either.
            _, queued = request_json(
                "POST", f"{base_url}/voice/speak", speak_payload()
            )
            status, _body = request_json(
                "POST", f"{base_url}/voice/queue/current/cancel", {}
            )
            assert status == 404
            assert _queue_status(app, queued["request_id"]) == "queued"
    finally:
        app.close()


def test_queue_listing_reports_playback_confirmation_telemetry(tmp_path: Path) -> None:
    app = make_voice_app(tmp_path)
    try:
        with running_server(app) as base_url:
            request_json("POST", f"{base_url}/voice/speak", speak_payload())
            status, body = request_json("GET", f"{base_url}/voice/queue?limit=5")

        assert status == 200
        rows = body["voice_queue"]
        assert len(rows) == 1
        assert rows[0]["playback_confirmed"] is False
    finally:
        app.close()


# --- GET /sessions -----------------------------------------------------------


def test_sessions_reports_owned_usage_and_honest_unknowns(tmp_path: Path) -> None:
    app = make_voice_app(tmp_path)
    try:
        with running_server(app) as base_url:
            status, body = request_json("GET", f"{base_url}/sessions")

        assert status == 200
        sessions = body["sessions"]
        assert sessions["read_only"] is True
        assert sessions["daemon"]["ok"] is True
        assert sessions["daemon"]["state"]
        assert sessions["usage"]["session_tokens_in"] == 0
        assert sessions["usage"]["session_tokens_out"] == 0
        # Metrics the daemon does not own are unknown, never a fake green.
        assert sessions["usage"]["cost"] == "unknown"
        assert sessions["usage"]["context_window"] == "unknown"
        assert sessions["brain"]["model"] == "unknown"
        assert sessions["brain"]["adapter"]
    finally:
        app.close()


def test_sessions_active_request_exposes_length_never_text(tmp_path: Path) -> None:
    app = make_voice_app(tmp_path)
    try:
        with running_server(app) as base_url:
            _, speaking = request_json(
                "POST", f"{base_url}/voice/speak", speak_payload()
            )
            _mark_status(app, speaking["request_id"], "speaking")

            status, body = request_json("GET", f"{base_url}/sessions")

        assert status == 200
        active = body["sessions"]["voice_queue"]["active_request"]
        assert active is not None
        assert active["id"] == speaking["request_id"]
        assert active["status"] == "speaking"
        assert active["text_length"] == len(SPEAK_TEXT)
        assert "text" not in active
        assert "text_preview" not in active
        assert SPEAK_TEXT not in json.dumps(body, ensure_ascii=False)
    finally:
        app.close()
