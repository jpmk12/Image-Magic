"""One place for the app's few outbound HTTPS requests (model downloads, weather).

Python verifies certificates with its own OpenSSL, which on Windows reads the
system's root store but, unlike a browser, never fetches a missing
intermediate certificate. Security software that inspects HTTPS adds its own
root as well. So requests here verify against the system store *and* a
bundled, current set of public roots (certifi), and a verification failure is
explained in plain words with a manual way out. Verification is never turned
off: every model file is also checked against a fixed SHA-256.
"""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request

CERT_HELP = (
    "The secure connection for this download could not be verified ({detail}). This usually means a security "
    "product or a work network on this computer inspects HTTPS traffic, or Windows is missing a root certificate "
    "(run Windows Update). Try again from a home Wi-Fi, or download the file yourself in a browser and put it where "
    "the app looks for it: {manual}")


def ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()           # the operating system's trusted roots
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())   # plus a current public set, for roots the OS lacks
    except Exception:
        pass
    return ctx


def urlopen(req, timeout: float = 60, manual: str = ""):
    """urllib.request.urlopen with the combined trust store and a readable error."""
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
            detail = getattr(reason, "verify_message", None) or str(reason or e)
            raise RuntimeError(CERT_HELP.format(detail=detail, manual=manual or "see the Activity log")) from e
        raise
