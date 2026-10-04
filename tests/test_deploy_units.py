import configparser
import importlib.util
import os
from pathlib import Path

UNIT_DIR = Path("deploy/systemd")


def _read(path):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(path)
    return parser


def test_expected_units_exist():
    names = {p.name for p in UNIT_DIR.iterdir()}
    assert {"home-hub-mailpoll.service", "home-hub-mailpoll.timer", "home-hub-telegram.service",
            "home-hub-banksync.service", "home-hub-banksync.timer"} <= names


def test_services_run_app_modules_from_the_checkout_without_privilege():
    for path in UNIT_DIR.glob("*.service"):
        service = _read(path)["Service"]
        assert service["WorkingDirectory"] == "/srv/home-hub/app"
        assert service["EnvironmentFile"] == "/srv/home-hub/app/.env"
        exec_start = service["ExecStart"].split()
        assert exec_start[:2] == ["/srv/home-hub/venv/bin/python", "-m"]
        assert importlib.util.find_spec(exec_start[2]) is not None, exec_start[2]
        text = path.read_text()
        assert "User=" not in text and "ExecStartPre=+" not in text


def test_every_timer_has_a_matching_oneshot_service():
    for timer in UNIT_DIR.glob("*.timer"):
        service = _read(timer.with_suffix(".service"))
        assert service["Service"]["Type"] == "oneshot"
        assert not service.has_section("Install")


def test_banksync_timer_runs_on_lisbon_time():
    text = (UNIT_DIR / "home-hub-banksync.timer").read_text()
    for hour in ("07:30:00", "13:00:00", "18:30:00"):
        assert f"OnCalendar=*-*-* {hour} Europe/Lisbon" in text


def test_install_script_is_executable():
    assert os.access("deploy/install_user_units.sh", os.X_OK)
