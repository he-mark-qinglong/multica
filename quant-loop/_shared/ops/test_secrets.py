"""Tests for _shared/ops/secrets.py (H13)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import logging

import pytest

from _shared.ops.secrets import (
    RedactFilter,
    Secret,
    load_secret,
    parse_env_file,
    redact,
)

KEY = "abcdef1234567890SECRET"


def test_repr_shows_only_first_four_chars():
    s = Secret(name="BINANCE_API_KEY", _value=KEY)
    assert repr(s) == "Secret(name='BINANCE_API_KEY', value='abcd****')"
    assert str(s) == repr(s)
    assert KEY not in repr(s)
    assert s.reveal() == KEY


def test_short_secret_is_fully_masked():
    s = Secret(name="PIN", _value="1234")
    assert "1234" not in repr(s)
    assert s.masked() == "****"


def test_secret_validation():
    with pytest.raises(ValueError):
        Secret(name="", _value=KEY)
    with pytest.raises(ValueError):
        Secret(name="K", _value="")


def test_parse_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text(
        "# comment\n"
        "\n"
        "BINANCE_API_KEY=abcdef\n"
        'QUOTED="with spaces"\n'
        "SINGLE='q'\n"
        "export EXPORTED=1\n"
        "NO_EQUALS_LINE\n"
        "DUP=first\n"
        "DUP=second\n"
    )
    parsed = parse_env_file(f)
    assert parsed == {
        "BINANCE_API_KEY": "abcdef",
        "QUOTED": "with spaces",
        "SINGLE": "q",
        "EXPORTED": "1",
        "DUP": "second",
    }


def test_load_priority_env_over_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"K=file-value\n")
    s = load_secret("K", env={"K": "env-value"}, file_path=f, prompt_fn=None)
    assert s.reveal() == "env-value"


def test_load_falls_back_to_file_then_prompt(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"K={KEY}\n")
    s = load_secret("K", env={}, file_path=f, prompt_fn=None)
    assert s.reveal() == KEY

    prompted = load_secret("MISSING", env={}, file_path=f,
                           prompt_fn=lambda prompt: "typed-value")
    assert prompted.reveal() == "typed-value"


def test_load_missing_raises_key_error(tmp_path):
    with pytest.raises(KeyError, match="MISSING"):
        load_secret("MISSING", env={}, file_path=tmp_path / ".env",
                    prompt_fn=None)


def test_redact_replaces_all_occurrences():
    s = Secret(name="K", _value=KEY)
    text = f"signed with {KEY} and again {KEY}"
    assert redact(text, [s]) == "signed with abcd**** and again abcd****"


def test_redact_filter_scrubs_log_records(caplog):
    s = Secret(name="K", _value=KEY)
    logger = logging.getLogger("test.secrets.h13")
    logger.setLevel(logging.DEBUG)
    logger.addFilter(RedactFilter([s]))

    with caplog.at_level(logging.DEBUG, logger="test.secrets.h13"):
        logger.info("direct key %s", KEY)
        logger.info("embedded payload sig=%s&x=1", KEY)
        try:
            raise RuntimeError(f"auth failed for {KEY}")
        except RuntimeError:
            logger.exception("boom")

    rendered = "\n".join(r.getMessage() for r in caplog.records)
    assert KEY not in rendered
    assert rendered.count("abcd****") >= 2
    # The formatter-facing msg was scrubbed too (args cleared):
    for r in caplog.records:
        assert KEY not in str(r.msg)
        assert KEY not in (r.exc_text or "")


def test_filter_without_secrets_passes_messages_through(caplog):
    logger = logging.getLogger("test.secrets.empty")
    logger.addFilter(RedactFilter([]))
    with caplog.at_level(logging.INFO, logger="test.secrets.empty"):
        logger.info("plain %s", "message")
    assert caplog.records[0].getMessage() == "plain message"
