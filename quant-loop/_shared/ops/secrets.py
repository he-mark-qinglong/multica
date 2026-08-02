"""API key management (H13).

Loads exchange API keys without ever persisting them:

  * **resolution priority** — process environment first, then a local
    ``.env`` file, then an interactive prompt (``getpass`` by default).
    First hit wins; the sources are tried in that exact order so a
    deployment can override a developer's local file via the environment.
  * **in-memory redaction** — keys live inside :class:`Secret`, whose
    ``repr``/``str`` show only the first 4 characters; the raw value is
    reachable only through the explicit ``.reveal()`` call.
  * **log safety** — :class:`RedactFilter` is a ``logging.Filter`` that
    scrubs every registered key value (and any substring built from one)
    out of every log record, so an accidental ``log.info(order)`` that
    embeds a signed payload cannot leak the key to the log file.
  * **no persistence** — this module exposes no write API at all. Keys
    are read from sources the operator controls and never written to any
    file by this code.

References:
- OWASP Secrets Management Cheat Sheet — secrets in environment over
  files over interactive entry; never in logs or VCS.
- NIST SP 800-57 — key material exposure minimization (redact by
  default, reveal only at the point of use).
"""
from __future__ import annotations

import getpass
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

MASK = "****"


@dataclass(frozen=True, repr=False)
class Secret:
    """An API key whose textual representation is always redacted.

    ``repr`` and ``str`` show at most the first 4 characters followed by
    ``****`` (values of 4 characters or fewer are fully masked). The raw
    value is accessible only via :meth:`reveal` — name it loudly so code
    review catches every point of use.
    """

    name: str
    _value: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must be non-empty")
        if not self._value:
            raise ValueError(f"secret {self.name} is empty")

    def reveal(self) -> str:
        """The raw key value. Use only at the signing/HTTP boundary."""
        return self._value

    def masked(self) -> str:
        """First 4 chars + mask; fully masked when too short to prefix."""
        if len(self._value) <= 4:
            return MASK
        return self._value[:4] + MASK

    def __repr__(self) -> str:
        return f"Secret(name={self.name!r}, value={self.masked()!r})"

    __str__ = __repr__


def parse_env_file(path) -> Mapping[str, str]:
    """Parse a .env file into a dict. Pure.

    Supports ``KEY=VALUE`` lines, ``#`` comments, blank lines, optional
    ``export `` prefix, and single/double-quoted values. Later duplicate
    keys win, matching dotenv convention.
    """
    out = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load_secret(
    name: str,
    env: Optional[Mapping[str, str]] = None,
    file_path=None,
    prompt_fn: Optional[Callable[[str], str]] = None,
) -> Secret:
    """Resolve a key by priority: environment > .env file > prompt.

    ``env`` defaults to ``os.environ``. ``prompt_fn`` defaults to
    ``getpass.getpass``; pass a stub in tests. Raises ``KeyError`` when
    no source yields a non-empty value and no prompt is available.
    """
    env = os.environ if env is None else env

    value = env.get(name)
    if value:
        return Secret(name=name, _value=value)

    if file_path is not None and Path(file_path).exists():
        value = parse_env_file(file_path).get(name)
        if value:
            return Secret(name=name, _value=value)

    if prompt_fn is None and file_path is None and env is os.environ:
        prompt_fn = getpass.getpass
    if prompt_fn is not None:
        value = prompt_fn(f"Enter {name}: ")
        if value:
            return Secret(name=name, _value=value)

    raise KeyError(f"secret {name!r} not found in env, file, or prompt")


def redact(text: str, secrets: Sequence[Secret]) -> str:
    """Replace every registered key value in ``text`` with its mask. Pure."""
    out = text
    for s in secrets:
        out = out.replace(s.reveal(), s.masked())
    return out


class RedactFilter(logging.Filter):
    """logging.Filter that scrubs registered key values from every record.

    Attach to a handler (or the root logger) once the process's keys are
    loaded; from then on any key value appearing in a log message — even
    inside an interpolated argument or an exception's str — is replaced
    by its masked form before the record is formatted.
    """

    def __init__(self, secrets: Sequence[Secret]):
        super().__init__()
        self._secrets = tuple(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        # Force eager formatting, redact the result, then clear args so
        # the formatter does not re-apply % to the scrubbed message.
        message = record.getMessage()
        scrubbed = redact(message, self._secrets)
        record.msg = scrubbed
        record.args = ()
        # exc_text is normally produced by the handler at emit time — too
        # late for a filter. Render the traceback eagerly here, redact it,
        # and clear exc_info so the formatter uses our scrubbed text.
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text, self._secrets)
        return True
