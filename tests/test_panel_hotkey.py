"""Global PTT hotkey logic (MASTER_PLAN §4a operator control).

Task 9 moved hotkey ownership into the daemon: the pure decision helpers live
in `dan.input.hotkey` (parse_hotkey, HotkeyEdgeDetector, trust probe) and the
panel keeps only display helpers (`fetch_effective_hotkey`) plus thin
re-exports. The panel posts NO PTT edges of its own anymore.
"""

from __future__ import annotations

import pytest

from dan.input.hotkey import (
    HotkeyEdgeDetector,
    HotkeySpecError,
    accessibility_trust_state,
    parse_hotkey,
)
from dan.panel.hotkey import fetch_effective_hotkey


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


# -- ownership guard ------------------------------------------------------
# Task 9: the daemon owns PTT edges. The panel module must not regain a
# client that POSTs /voice/ptt/* on key events.

def test_panel_module_owns_no_ptt_client():
    import dan.panel.hotkey as panel_hotkey

    assert not hasattr(panel_hotkey, "PttHotkeyClient")


def test_daemon_side_logic_is_importable_from_dan_input():
    from dan.input.hotkey import PTT_SOURCE

    assert PTT_SOURCE == "global_hotkey"
