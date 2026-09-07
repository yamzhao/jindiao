"""Per-client TLS trust selection, without patching Python's global SSL state."""

from __future__ import annotations

import ssl
from pathlib import Path

import certifi


def create_tls_context(
    trust_store: str = "certifi", *, ca_file: Path | None = None
) -> ssl.SSLContext:
    if ca_file is not None and trust_store != "certifi":
        raise ValueError("Explicit certificate files require the certifi TLS trust store")
    if trust_store == "certifi":
        context = ssl.create_default_context(cafile=certifi.where())
        if ca_file is not None:
            context.load_verify_locations(cafile=ca_file)
            # Keep validation rooted in a complete chain, not an intermediate anchor.
            context.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
        return context
    if trust_store == "system":
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    raise ValueError("Unsupported TLS trust store")
