"""The public API must not answer one visitor at a time.

Measured 2026-09-11 against production: gunicorn ran with its defaults, one
sync worker and a 30-second timeout. Four /score requests sent together took
6, 11, 17 and 24 seconds; across a 113-token comparison the mean was 39.8 s and
the slowest 81.9 s, and the logs showed WORKER TIMEOUT.

The first fix put the flags in the Procfile. Production served that commit and
still logged "Using worker: sync", so the settings live in gunicorn.conf.py,
which gunicorn reads from the working directory under any start command.
"""

from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return runpy.run_path(str(ROOT / "gunicorn.conf.py"))


def test_config_file_uses_threads():
    config = _config()
    assert config["worker_class"] == "gthread"
    assert config["threads"] >= 4


def test_one_worker_so_caches_are_shared():
    assert _config()["workers"] == 1


def test_timeout_outlasts_a_slow_score():
    # dexscreener 20 s + holder intel 8 s + engine 8 s + contract reads.
    assert _config()["timeout"] >= 90


def test_procfile_does_not_override_the_config_file():
    web = next(line for line in (ROOT / "Procfile").read_text(encoding="utf-8").splitlines()
               if line.startswith("web:"))
    for flag in ("--worker-class", "-k ", "--workers", "-w ", "--threads", "--timeout", "-t ", "--config", "-c "):
        assert flag not in web, f"{flag.strip()} on the command line would override gunicorn.conf.py"
