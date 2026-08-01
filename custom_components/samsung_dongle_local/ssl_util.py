"""Turn pasted PEM text into SSL contexts.

Python's ``load_cert_chain`` only reads from disk, so the PEMs are written to a
short-lived temp file with restrictive permissions, loaded, and removed. The
material itself lives in the config entry, never on disk permanently.

Everything here does blocking file I/O -- call it from an executor.
"""

from __future__ import annotations

import contextlib
import os
import ssl
import tempfile

from .api import build_client_ssl_context, build_server_ssl_context


class InvalidCertificate(Exception):
    """The supplied PEM material is unusable."""


@contextlib.contextmanager
def _pem_files(cert_pem: str, key_pem: str | None):
    """Materialise PEM text as files, then shred the paths."""
    paths: list[str] = []
    try:
        fd, cert_path = tempfile.mkstemp(suffix=".pem")
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(cert_pem.strip() + "\n")
        os.chmod(cert_path, 0o600)
        paths.append(cert_path)

        key_path: str | None = None
        if key_pem and key_pem.strip():
            fd, key_path = tempfile.mkstemp(suffix=".key")
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(key_pem.strip() + "\n")
            os.chmod(key_path, 0o600)
            paths.append(key_path)

        yield cert_path, key_path
    finally:
        for path in paths:
            with contextlib.suppress(OSError):
                os.unlink(path)


def build_contexts(cert_pem: str, key_pem: str | None) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """Return ``(client_context, server_context)``.

    The server context is only needed during pairing -- the appliance connects
    back to us over TLS and we have to present *something*. It does not
    validate what we present, so the same material serves both roles.
    """
    try:
        with _pem_files(cert_pem, key_pem) as (cert_path, key_path):
            client = build_client_ssl_context(cert_path, key_path)
            server = build_server_ssl_context(cert_path, key_path)
    except (ssl.SSLError, OSError, ValueError) as err:
        raise InvalidCertificate(str(err)) from err
    return client, server
