"""Task 11: after install exactly one PRODUCT launchd label exists.

Legacy jobs (com.ozzy.jarvisd, old broker, standup, menubar) are not product
labels: the installer neither writes nor deletes them — retiring them is
journaled cutover work (Task 12/14), never a side effect of install.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from dan.install import InstallPlan
from dan.install.launchd import product_launchd_labels

LEGACY_PLISTS = {
    "com.ozzy.jarvisd.plist": "com.ozzy.jarvisd",
    "com.ozzy.voice-standup.plist": "com.ozzy.voice-standup",
    "com.ozzy.menubar-controller.plist": "com.ozzy.menubar-controller",
    "com.dan.voice-broker.plist": "com.dan.voice-broker",
    "ai.openclaw.gateway.plist": "ai.openclaw.gateway",
}


def _seed_legacy(agents: Path) -> dict[str, bytes]:
    agents.mkdir(parents=True, exist_ok=True)
    seeded: dict[str, bytes] = {}
    for filename, label in LEGACY_PLISTS.items():
        payload = plistlib.dumps({"Label": label, "ProgramArguments": ["/usr/bin/true"]})
        (agents / filename).write_bytes(payload)
        seeded[filename] = payload
    return seeded


def _install(home: Path, tmp_path: Path) -> None:
    plan = InstallPlan(home=home)
    staging = tmp_path / "staging"
    plan.render(staging)
    plan.verify(staging)
    plan.apply(backup_root=tmp_path / "backups")


@pytest.fixture
def installed_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    _seed_legacy(home / "Library" / "LaunchAgents")
    _install(home, tmp_path)
    return home


def test_install_has_one_product_launchd_label(installed_home: Path) -> None:
    labels = product_launchd_labels(installed_home / "Library" / "LaunchAgents")
    assert labels == ["com.dan.dand"]


def test_legacy_plists_are_left_byte_identical(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    agents = home / "Library" / "LaunchAgents"
    seeded = _seed_legacy(agents)
    _install(home, tmp_path)
    for filename, payload in seeded.items():
        assert (agents / filename).read_bytes() == payload, filename


def test_product_plist_shape(installed_home: Path) -> None:
    plist_path = installed_home / "Library" / "LaunchAgents" / "com.dan.dand.plist"
    data = plistlib.loads(plist_path.read_bytes())
    assert data["Label"] == "com.dan.dand"
    assert data["RunAtLoad"] is True
    # RestartCoordinator exits 86 and relies on launchd resurrection.
    assert data["KeepAlive"] is True
    program = data["ProgramArguments"]
    assert program == [str(installed_home / ".dan" / "bin" / "dand")]
    assert str(installed_home / ".dan" / "logs") in data["StandardOutPath"]
    assert str(installed_home / ".dan" / "logs") in data["StandardErrorPath"]
    text = plist_path.read_text(encoding="utf-8")
    for secret_marker in ("TOKEN", "SECRET", "PASSWORD", "api-key"):
        assert secret_marker.lower() not in text.lower()


def test_no_launchd_install_writes_no_agent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    plan = InstallPlan(home=home, include_launchd=False)
    staging = tmp_path / "staging"
    plan.render(staging)
    plan.verify(staging)
    plan.apply(backup_root=tmp_path / "backups")
    agents = home / "Library" / "LaunchAgents"
    assert not agents.exists() or list(agents.glob("*.plist")) == []


def test_installer_writes_no_second_broker_tts_or_hotkey_job(installed_home: Path) -> None:
    agents = installed_home / "Library" / "LaunchAgents"
    written = [
        path
        for path in agents.glob("*.plist")
        if path.name not in LEGACY_PLISTS
    ]
    assert [path.name for path in written] == ["com.dan.dand.plist"]
