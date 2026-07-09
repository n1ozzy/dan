"""Global PTT hotkey logic (panel shell, MASTER_PLAN §4a operator control).

The native NSEvent monitor is thin and untestable without a keyboard +
Accessibility; all decisions live in pure helpers tested here:
  - parse_hotkey: "left_cmd+left_shift" -> a macOS device-modifier bitmask
  - HotkeyEdgeDetector: a stream of modifier flags -> "down"/"up" edges
  - PttHotkeyClient: an edge -> a POST to /voice/ptt/{down,up}
"""

from __future__ import annotations

import pytest

from jarvis.panel.hotkey import (
    HotkeyEdgeDetector,
    HotkeySpecError,
    PttHotkeyClient,
    accessibility_trust_state,
    fetch_effective_hotkey,
    parse_hotkey,
)


# -- parse_hotkey ---------------------------------------------------------

def test_parse_single_modifier():
    assert parse_hotkey("left_cmd") == 0x000008


def test_parse_combo_is_bit_union_order_insensitive():
    assert parse_hotkey("left_cmd+left_shift") == 0x000008 | 0x000002
    assert parse_hotkey("left_shift+left_cmd") == 0x000008 | 0x000002


def test_parse_is_whitespace_and_case_tolerant():
    assert parse_hotkey("  Left_Cmd +  LEFT_SHIFT ") == 0x000008 | 0x000002


def test_parse_empty_is_zero_meaning_disabled():
    assert parse_hotkey("") == 0
    assert parse_hotkey("   ") == 0


def test_parse_rejects_unknown_token():
    with pytest.raises(HotkeySpecError):
        parse_hotkey("left_cmd+banana")


def test_parse_alt_is_alias_for_option():
    assert parse_hotkey("left_alt") == parse_hotkey("left_option")


def test_parse_consumes_full_combo_not_a_truncated_prefix():
    # Guards the "field shows 'le'" class of bug: the parser must key off the
    # WHOLE '+'-split tokens, never a truncated prefix. A partial token is a
    # hard error, not a silent no-op that would disable the hotkey unseen.
    full = parse_hotkey("left_cmd+left_shift")
    assert full == 0x000008 | 0x000002
    assert parse_hotkey("left_shift + left_cmd") == full  # order-insensitive
    with pytest.raises(HotkeySpecError):
        parse_hotkey("le")  # a truncated prefix is rejected loudly


# -- accessibility_trust_state -------------------------------------------
# The global flagsChanged monitor only fires when the panel process is
# trusted for Accessibility. This helper turns that silent gate — the reason
# the button works but the hotkey stays dead — into an observable state.

def test_trust_state_trusted_when_checker_true():
    assert accessibility_trust_state(checker=lambda: True) == "trusted"


def test_trust_state_untrusted_when_checker_false():
    assert accessibility_trust_state(checker=lambda: False) == "untrusted"


def test_trust_state_unknown_when_probe_raises():
    def boom():
        raise RuntimeError("AX probe failed")

    # a probe failure must degrade to 'unknown', never crash panel boot
    assert accessibility_trust_state(checker=boom) == "unknown"


def test_trust_state_unknown_when_framework_absent(monkeypatch):
    # Default path: with no ApplicationServices framework importable (the
    # panel extra may be minimal), the helper reports 'unknown' rather than
    # a false claim of trust.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "ApplicationServices":
            raise ModuleNotFoundError("No module named 'ApplicationServices'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert accessibility_trust_state() == "unknown"


# -- fetch_effective_hotkey ----------------------------------------------
# The panel UI writes voice.ptt_hotkey to the daemon's DB (GET /settings),
# not to the static TOML the shell boots from. The native monitor must bind
# to that live value or it watches a stale combo and never fires — the exact
# "button works, hotkey doesn't" bug.

def test_effective_hotkey_prefers_live_daemon_value_over_config():
    def getter(base, token):
        assert token == "tok"
        return {"settings": {"voice.ptt_hotkey": "left_cmd+left_shift"}}

    assert (
        fetch_effective_hotkey("http://127.0.0.1:41741", "tok", getter=getter)
        == "left_cmd+left_shift"
    )


def test_effective_hotkey_strips_surrounding_whitespace():
    getter = lambda base, token: {"settings": {"voice.ptt_hotkey": "  left_cmd+left_shift "}}
    assert (
        fetch_effective_hotkey("http://x", None, getter=getter)
        == "left_cmd+left_shift"
    )


def test_effective_hotkey_none_when_key_absent():
    getter = lambda base, token: {"settings": {"voice.ptt_mode": "hold"}}
    assert fetch_effective_hotkey("http://x", None, getter=getter) is None


def test_effective_hotkey_none_when_value_blank_or_not_string():
    assert fetch_effective_hotkey(
        "http://x", None, getter=lambda b, t: {"settings": {"voice.ptt_hotkey": ""}}
    ) is None
    assert fetch_effective_hotkey(
        "http://x", None, getter=lambda b, t: {"settings": {"voice.ptt_hotkey": 42}}
    ) is None


def test_effective_hotkey_none_on_malformed_payload():
    assert fetch_effective_hotkey(
        "http://x", None, getter=lambda b, t: {"nope": 1}
    ) is None
    assert fetch_effective_hotkey(
        "http://x", None, getter=lambda b, t: "not-a-dict"
    ) is None


def test_effective_hotkey_swallows_transport_error():
    def boom(base, token):
        raise OSError("daemon down")

    # a dead daemon must not crash panel boot — caller falls back to config
    assert fetch_effective_hotkey("http://x", "tok", getter=boom) is None


# -- HotkeyEdgeDetector ---------------------------------------------------

def _detect(required, flag_stream):
    det = HotkeyEdgeDetector(required)
    return [det.update(flags) for flags in flag_stream]


def test_edge_fires_down_only_when_all_required_bits_present():
    required = 0x8 | 0x2  # left_cmd + left_shift
    # only cmd -> nothing; add shift -> down; release shift -> up
    assert _detect(required, [0x8, 0x8 | 0x2, 0x8]) == [None, "down", "up"]


def test_edge_down_is_edge_triggered_not_level():
    required = 0x8
    # held across two polls: down once, then silence until release
    assert _detect(required, [0x8, 0x8, 0x0]) == ["down", None, "up"]


def test_edge_ignores_extra_unrelated_modifiers():
    required = 0x8 | 0x2
    # a stray control bit (0x1) alongside the combo must not break the match
    assert _detect(required, [0x8 | 0x2 | 0x1, 0x1]) == ["down", "up"]


def test_edge_disabled_when_required_is_zero():
    assert _detect(0, [0x0, 0x8, 0x8 | 0x2]) == [None, None, None]


def test_edge_release_needs_only_one_required_bit_gone():
    required = 0x8 | 0x2
    # drop cmd but keep shift -> the combo is broken -> up
    assert _detect(required, [0x8 | 0x2, 0x2]) == ["down", "up"]


# -- PttHotkeyClient ------------------------------------------------------

class _FakePoster:
    def __init__(self):
        self.calls = []

    def __call__(self, url, *, data, headers):
        self.calls.append({"url": url, "data": data, "headers": headers})


def test_client_down_posts_to_ptt_down_with_source_and_token():
    poster = _FakePoster()
    client = PttHotkeyClient(
        "http://127.0.0.1:41741",
        "tok123",
        poster=poster,
        health_checker=lambda: True,
    )
    client.down()
    assert len(poster.calls) == 1
    call = poster.calls[0]
    assert call["url"] == "http://127.0.0.1:41741/voice/ptt/down"
    assert call["headers"]["X-Jarvis-Token"] == "tok123"
    assert b'"global_hotkey"' in call["data"]


def test_client_up_posts_to_ptt_up():
    poster = _FakePoster()
    client = PttHotkeyClient(
        "http://127.0.0.1:41741/",
        "tok",
        poster=poster,
        health_checker=lambda: True,
    )
    client.up()
    # trailing slash on base must not double up in the path
    assert poster.calls[0]["url"] == "http://127.0.0.1:41741/voice/ptt/up"


def test_client_skips_ptt_when_backend_is_unhealthy():
    poster = _FakePoster()
    client = PttHotkeyClient(
        "http://127.0.0.1:41741",
        "tok",
        poster=poster,
        health_checker=lambda: False,
    )

    client.down()
    client.up()

    assert poster.calls == []


def test_client_swallows_poster_errors():
    def boom(url, *, data, headers):
        raise OSError("daemon down")

    client = PttHotkeyClient(
        "http://127.0.0.1:41741",
        "tok",
        poster=boom,
        health_checker=lambda: True,
    )
    # a dead daemon must never crash the panel's key handler
    client.down()
    client.up()


def test_client_dispatch_maps_edge_to_method():
    poster = _FakePoster()
    client = PttHotkeyClient(
        "http://127.0.0.1:41741",
        "tok",
        poster=poster,
        health_checker=lambda: True,
    )
    client.dispatch("down")
    client.dispatch("up")
    client.dispatch(None)  # no edge -> no call
    assert [c["url"].rsplit("/", 1)[-1] for c in poster.calls] == ["down", "up"]
