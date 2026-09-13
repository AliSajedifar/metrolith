"""UTF-8 transport owned by CLI invocation, with no import-time stream changes."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import sys


class DeliveryError(OSError):
    """The CLI could not deliver text to its caller."""


class _Stream:
    def __init__(self, stream, *, machine=False):
        self.stream = stream
        self.machine = machine
        self.failed = False
        self.binary = None
        reconfigure = getattr(stream, 'reconfigure', None)
        if callable(reconfigure) and not getattr(stream, 'closed', False):
            try:
                reconfigure(encoding='utf-8', errors='strict', newline='')
            except (OSError, ValueError, TypeError):
                self.binary = getattr(stream, 'buffer', None)
        elif not callable(reconfigure):
            self.binary = getattr(stream, 'buffer', None)

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def write(self, value):
        if self.failed:
            raise DeliveryError('output stream is unavailable')
        try:
            if self.binary is not None:
                self.binary.write(value.encode('utf-8', errors='strict'))
            else:
                try:
                    self.stream.write(value)
                except UnicodeEncodeError:
                    if self.machine:
                        raise
                    # Text-only injected terminals can expose an explicit,
                    # reversible JSON escape representation. Machine data never
                    # takes this fallback and never uses replacement/ignore.
                    self.stream.write('[Unicode text, JSON escaped] ' + json.dumps(value, ensure_ascii=True) + '\n')
            return len(value)
        except (OSError, ValueError) as exc:
            self.failed = True
            raise DeliveryError(f'CLI output delivery failed: {type(exc).__name__}: {exc}') from exc

    def flush(self):
        if self.failed:
            raise DeliveryError('output stream is unavailable')
        try:
            (self.binary if self.binary is not None else self.stream).flush()
        except (OSError, ValueError) as exc:
            self.failed = True
            raise DeliveryError(f'CLI output delivery failed: {type(exc).__name__}: {exc}') from exc


def _machine_stdout(argv):
    for index, item in enumerate(argv):
        if item.startswith('--format='):
            return item.split('=', 1)[1] in {'json', 'sarif'}
        if item == '--format' and index + 1 < len(argv):
            return argv[index + 1] in {'json', 'sarif'}
    return bool(argv and argv[0] in {'schema', 'doctor'}) or argv[:2] in (
        ['policy', 'metrics'], ['policy', 'rules'], ['policy', 'default-policy'],
    )


@contextmanager
def cli_streams(argv):
    original_out, original_err = sys.stdout, sys.stderr
    out = _Stream(original_out, machine=_machine_stdout(argv))
    err = _Stream(original_err)
    sys.stdout, sys.stderr = out, err
    try:
        yield out, err
    finally:
        sys.stdout, sys.stderr = original_out, original_err


def report_delivery_failure(error, err):
    if not err.failed:
        try:
            err.write(f'[ERROR] {error}\n')
            err.flush()
        except DeliveryError:
            pass


def silence_failed_standard_stream(stream):
    """Prevent interpreter shutdown from flushing the same broken OS pipe."""
    if stream.failed and stream.stream in (sys.__stdout__, sys.__stderr__):
        try:
            fd = stream.stream.fileno()
            with open(os.devnull, 'wb') as sink:
                os.dup2(sink.fileno(), fd)
        except (OSError, ValueError, AttributeError):
            pass
