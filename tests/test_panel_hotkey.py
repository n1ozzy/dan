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
    client = PttHotkeyClient("http://127.0.0.1:41741", "tok123", poster=poster)
    client.down()
    assert len(poster.calls) == 1
    call = poster.calls[0]
    assert call["url"] == "http://127.0.0.1:41741/voice/ptt/down"
    assert call["headers"]["X-Jarvis-Token"] == "tok123"
    assert b'"global_hotkey"' in call["data"]


def test_client_up_posts_to_ptt_up():
    poster = _FakePoster()
    client = PttHotkeyClient("http://127.0.0.1:41741/", "tok", poster=poster)
    client.up()
    # trailing slash on base must not double up in the path
    assert poster.calls[0]["url"] == "http://127.0.0.1:41741/voice/ptt/up"


def test_client_swallows_poster_errors():
    def boom(url, *, data, headers):
        raise OSError("daemon down")

    client = PttHotkeyClient("http://127.0.0.1:41741", "tok", poster=boom)
    # a dead daemon must never crash the panel's key handler
    client.down()
    client.up()


def test_client_dispatch_maps_edge_to_method():
    poster = _FakePoster()
    client = PttHotkeyClient("http://127.0.0.1:41741", "tok", poster=poster)
    client.dispatch("down")
    client.dispatch("up")
    client.dispatch(None)  # no edge -> no call
    assert [c["url"].rsplit("/", 1)[-1] for c in poster.calls] == ["down", "up"]
