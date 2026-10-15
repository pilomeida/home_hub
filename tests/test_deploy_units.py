import configparser
import importlib.util
import os
import subprocess
from pathlib import Path

UNIT_DIR = Path("deploy/systemd")


def _read(path):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(path)
    return parser


RETIRED = ["home-hub-wikilint", "home-hub-banksync", "home-hub-reminder", "home-hub-mailpoll"]


def test_expected_units_exist():
    names = {p.name for p in UNIT_DIR.iterdir()}
    assert names == {"home-hub-scheduler.service", "home-hub-scheduler.timer", "home-hub-telegram.service"}


def test_the_fixed_timers_that_moved_onto_the_hubs_scheduler_are_gone_and_retired_on_the_server():
    names = {p.name for p in UNIT_DIR.iterdir()}
    installer = Path("deploy/install_user_units.sh").read_text()
    retired_line = installer.split("RETIRED_UNITS=")[1].splitlines()[0]
    for stem in RETIRED:
        for suffix in (".timer", ".service"):
            assert stem + suffix not in names                       # not in the repo, so never reinstalled
            assert stem + suffix in retired_line                    # stopped and removed from the server, so a job never runs twice
    assert "home-hub-scheduler" not in retired_line and "home-hub-telegram" not in retired_line


def test_install_script_retires_before_it_installs():
    installer = Path("deploy/install_user_units.sh").read_text()
    assert installer.index("for unit in $RETIRED_UNITS") < installer.index('install -m 0644 "$unit" "$DEST/"')


def test_install_script_is_valid_bash():
    result = subprocess.run(["bash", "-n", "deploy/install_user_units.sh"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


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


def test_scheduler_timer_fires_every_five_minutes_on_the_clock_and_catches_up():
    from app.services.schedule import CHECK_INTERVAL_MINUTES
    timer = _read(UNIT_DIR / "home-hub-scheduler.timer")
    text = (UNIT_DIR / "home-hub-scheduler.timer").read_text()
    assert f"OnCalendar=*:0/{CHECK_INTERVAL_MINUTES}" in text and CHECK_INTERVAL_MINUTES == 5   # bank slots (07:30) start on time
    assert "Persistent=true" in text and "OnBootSec=" in text
    assert timer["Install"]["WantedBy"] == "timers.target"
    assert "OnUnitActiveSec" not in text


def test_scheduler_service_is_a_oneshot_so_a_slow_run_is_never_started_twice_by_systemd():
    service = _read(UNIT_DIR / "home-hub-scheduler.service")
    assert service["Service"]["Type"] == "oneshot" and not service.has_section("Install")
    assert service["Service"]["ExecStart"].endswith("-m app.jobs.scheduler")


def test_install_script_is_executable():
    assert os.access("deploy/install_user_units.sh", os.X_OK)
