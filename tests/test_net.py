"""Outbound HTTPS: combined trust store, readable certificate errors, manual fallback."""
import ssl
import urllib.error
import urllib.request

import pytest

from image_magic import faces, net


def test_context_has_system_and_bundled_roots():
    ctx = net.ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] > 100


def test_certificate_errors_are_explained(monkeypatch):
    def boom(req, timeout, context):
        raise urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get issuer certificate"))
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as e:
        net.urlopen(urllib.request.Request("https://example.invalid/x"), manual="save it as C:\\models\\x.zip")
    msg = str(e.value)
    assert "could not be verified" in msg and "C:\\models\\x.zip" in msg and "inspects HTTPS" in msg
    # other network errors pass through unchanged
    def down(req, timeout, context):
        raise urllib.error.URLError(ConnectionRefusedError())
    monkeypatch.setattr(urllib.request, "urlopen", down)
    with pytest.raises(urllib.error.URLError):
        net.urlopen(urllib.request.Request("https://example.invalid/x"))


def test_manual_face_pack_is_used_and_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_MAGIC_HOME", str(tmp_path))
    d = faces.model_dir(); d.mkdir(parents=True)
    (d / "buffalo_l.zip").write_bytes(b"PK not the real pack" * 1000)
    monkeypatch.setattr(faces, "PACK_SIZE", 20000)          # so no download is attempted
    with pytest.raises(RuntimeError, match="integrity"):
        faces.download_model()
    assert not (d / "buffalo_l.zip").exists() and not (d / "pack.zip.part").exists()
