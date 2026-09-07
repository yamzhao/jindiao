"""Explicit trust selection must never disable certificate verification."""

from __future__ import annotations

import importlib
import importlib.util
import ssl
from pathlib import Path

import pytest


@pytest.mark.parametrize("backend", ["certifi", "system"])
def test_tls_context_verifies_certificates_without_global_patching(backend: str) -> None:
    assert importlib.util.find_spec("jindiao.security.tls") is not None
    tls = importlib.import_module("jindiao.security.tls")
    original = ssl.SSLContext
    context = tls.create_tls_context(backend)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert ssl.SSLContext is original


def test_unknown_backend_cannot_be_used_to_disable_tls() -> None:
    assert importlib.util.find_spec("jindiao.security.tls") is not None
    tls = importlib.import_module("jindiao.security.tls")
    with pytest.raises(ValueError, match="Unsupported TLS trust store"):
        tls.create_tls_context("insecure")


def test_explicit_intermediate_keeps_roots_and_requires_complete_chain() -> None:
    tls = importlib.import_module("jindiao.security.tls")
    baseline = tls.create_tls_context("certifi")
    context = tls.create_tls_context(
        "certifi", ca_file=Path("deploy/bff/certs/globalsign-rsa-ov-2018.pem")
    )
    assert context.cert_store_stats()["x509_ca"] == baseline.cert_store_stats()["x509_ca"] + 1
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert not context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN


def test_missing_certificate_file_fails_closed(tmp_path: Path) -> None:
    tls = importlib.import_module("jindiao.security.tls")
    with pytest.raises(FileNotFoundError):
        tls.create_tls_context("certifi", ca_file=tmp_path / "missing.pem")


def test_invalid_certificate_file_fails_closed(tmp_path: Path) -> None:
    tls = importlib.import_module("jindiao.security.tls")
    path = tmp_path / "invalid.pem"
    path.write_text("not a certificate")
    with pytest.raises(ssl.SSLError):
        tls.create_tls_context("certifi", ca_file=path)


def test_extra_certificates_require_explicit_openssl_backend() -> None:
    tls = importlib.import_module("jindiao.security.tls")
    with pytest.raises(ValueError, match="certifi"):
        tls.create_tls_context(
            "system", ca_file=Path("deploy/bff/certs/globalsign-rsa-ov-2018.pem")
        )
