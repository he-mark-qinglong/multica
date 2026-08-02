"""Tests for _shared/ops/deploy.py (H11)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import plistlib

import pytest

from _shared.ops.deploy import (
    DeploySpec,
    placeholder_env,
    render_launchd_plist,
    render_systemd_unit,
    write_unit,
)


def _spec(**kw):
    base = dict(
        strategy="mm_btc",
        argv=("/usr/bin/python3", "-m", "paper.runner", "--config", "mm.yaml"),
        working_dir="/opt/quant-loop",
        log_dir="/var/log/quant-loop",
        env={"MODE": "live", **placeholder_env("BINANCE_API_KEY")},
    )
    base.update(kw)
    return DeploySpec(**base)


def test_systemd_unit_restart_and_logs():
    text = render_systemd_unit(_spec(user="quant"))
    assert "Restart=on-failure" in text
    assert "RestartSec=5" in text
    assert "StandardOutput=append:/var/log/quant-loop/mm_btc.out.log" in text
    assert "StandardError=append:/var/log/quant-loop/mm_btc.err.log" in text
    assert "User=quant" in text
    assert "WorkingDirectory=/opt/quant-loop" in text
    assert "ExecStart=/usr/bin/python3 -m paper.runner --config mm.yaml" in text
    assert "Description=quant-loop strategy mm_btc" in text


def test_systemd_unit_env_placeholders_no_secret_material():
    text = render_systemd_unit(_spec())
    assert 'Environment="BINANCE_API_KEY=${BINANCE_API_KEY}"' in text
    assert 'Environment="MODE=live"' in text


def test_systemd_unit_quotes_argv_with_spaces():
    spec = _spec(argv=("/usr/bin/python3", "-c", "print('hello world')"))
    text = render_systemd_unit(spec)
    assert "'print('" in text and "hello world" in text


def test_launchd_plist_parses_and_restarts_on_failure():
    spec = _spec()
    plist = plistlib.loads(render_launchd_plist(spec).encode())
    assert plist["Label"] == "com.quant-loop.mm_btc"
    assert list(plist["ProgramArguments"]) == list(spec.argv)
    assert plist["WorkingDirectory"] == "/opt/quant-loop"
    # Restart on failure, not on clean exit:
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["ThrottleInterval"] == 5
    assert plist["RunAtLoad"] is True
    assert plist["StandardOutPath"] == "/var/log/quant-loop/mm_btc.out.log"
    assert plist["StandardErrorPath"] == "/var/log/quant-loop/mm_btc.err.log"
    assert plist["EnvironmentVariables"]["BINANCE_API_KEY"] == "${BINANCE_API_KEY}"


def test_launchd_plist_escapes_xml():
    spec = _spec(argv=("/bin/echo", "a<b>&\"c"))
    plist = plistlib.loads(render_launchd_plist(spec).encode())
    assert plist["ProgramArguments"][1] == 'a<b>&"c'


def test_write_unit_infers_platform_from_suffix(tmp_path):
    spec = _spec()
    svc = write_unit(spec, tmp_path / "units" / "mm_btc.service")
    plist = write_unit(spec, tmp_path / "units" / "com.quant-loop.mm_btc.plist")
    assert "[Service]" in svc.read_text()
    assert plistlib.loads(plist.read_bytes())["Label"] == "com.quant-loop.mm_btc"


def test_write_unit_explicit_platform(tmp_path):
    spec = _spec()
    out = write_unit(spec, tmp_path / "unit.txt", platform="launchd")
    assert out.read_text().startswith("<?xml")
    with pytest.raises(ValueError, match="platform"):
        write_unit(spec, tmp_path / "x.service", platform="windows")


def test_placeholder_env_shape():
    assert placeholder_env("A", "B") == {"A": "${A}", "B": "${B}"}


def test_spec_validation():
    with pytest.raises(ValueError):
        DeploySpec(strategy="", argv=("x",), working_dir="/w", log_dir="/l")
    with pytest.raises(ValueError):
        DeploySpec(strategy="a", argv=(), working_dir="/w", log_dir="/l")
    with pytest.raises(ValueError):
        DeploySpec(strategy="a", argv=("x",), working_dir="/w", log_dir="/l",
                   restart_sec=-1)
