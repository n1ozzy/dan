"""Voice turns must roll into ONE conversation, not a fresh session each time.

Ozzy's report: every spoken message started a new session, so DAN never
remembered the previous utterance. `_start_voice_turn` passed no
conversation_id, so `get_or_create(None)` minted a fresh conversation per turn
(74 turns had spread across 72 conversations). The daemon must keep a rolling
voice conversation: the first utterance mints it, later ones continue it.
"""

from __future__ import annotations

from pathlib import Path

from dan.daemon.app import create_daemon_app
from tests.test_api_smoke import config_text


def _voice_app(tmp_path: Path):
    config_path = tmp_path / "dan.toml"
    config_path.write_text(
        config_text(tmp_path / "home" / "dan.db"), encoding="utf-8"
    )
    app = create_daemon_app(config_path)
    app.start()
    return app


def test_consecutive_voice_turns_share_one_conversation(tmp_path: Path) -> None:
    app = _voice_app(tmp_path)
    try:
        first = app._start_voice_turn("pierwsza wiadomosc glosowa")
        second = app._start_voice_turn("druga wiadomosc glosowa")

        assert first.conversation_id == second.conversation_id
    finally:
        app.close()
