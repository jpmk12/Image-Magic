"""Phone access: the app on the laptop, viewed from a phone on the same Wi-Fi.

Off by default; the app stays loopback-only until you turn it on in Settings.
When on, a second listener on the Wi-Fi address serves the same pages over
HTTPS with a certificate made on this computer (the phone's browser warns about
it once; that is expected for a home-made certificate). A phone gets in only
with the six-digit pairing code shown in Settings, which changes every ten
minutes and dies after five wrong tries. Each paired phone gets its own token;
"Disconnect all phones" revokes them. Phones can browse, replay, ask and record
voice notes, but cannot change settings, start syncs, export or quit.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import secrets
import socket
import threading
import time
from pathlib import Path

from .settings import settings_dir

CODE_TTL = 600
MAX_TRIES = 5
# What a paired phone may do: look, relive, ask, speak, and add its own photos. Everything else
# (settings, syncs, curation, exports, the pairing code, the activity log) is the laptop's.
PHONE_GET = ("/api/status", "/api/photos", "/api/photo/", "/api/facets", "/api/trips", "/api/events", "/api/albums",
             "/api/replay", "/api/map", "/api/people", "/api/timeline", "/api/story", "/api/memories", "/api/shuffle",
             "/api/similar/", "/api/review", "/api/context/", "/api/home", "/api/places/", "/api/daily", "/api/voice/",
             "/api/compare/options", "/api/ids", "/api/family", "/api/flying",
             "/media/thumb/", "/media/view/", "/media/face/", "/media/voice/", "/media/export/")
PHONE_POST = ("/api/ask", "/api/voice", "/api/voice/delete", "/api/contribute", "/api/compare", "/api/poster")
PHONE_GET_EXACT_DENY = ("/api/people/book", "/api/people/gif", "/api/review-queue", "/api/phone", "/api/log", "/api/settings", "/api/book")


def lan_ips() -> list[str]:
    """This computer's private IPv4 addresses (Wi-Fi, Ethernet), best first."""
    found: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))      # no packet is sent; picks the default route's address
        ip = s.getsockname()[0]
        s.close()
        if ipaddress.ip_address(ip).is_private:
            found.append(ip)
    except (OSError, ValueError):
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and ipaddress.ip_address(ip).is_private:
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith("127.")]


def cert_files() -> tuple[Path, Path]:
    d = settings_dir()
    return d / "phone-cert.pem", d / "phone-key.pem"


def ensure_certificate(ips: list[str]) -> tuple[Path, Path]:
    """A self-signed certificate for this computer's addresses, made once and reused (10 years)."""
    cert, key = cert_files()
    if cert.exists() and key.exists():
        try:
            from cryptography import x509
            c = x509.load_pem_x509_certificate(cert.read_bytes())
            have = {str(v) for v in c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)}
            if set(ips) <= have and c.not_valid_after_utc > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30):
                return cert, key
        except Exception:
            pass
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    priv = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Image Magic on this computer")])
    now = dt.datetime.now(dt.timezone.utc)
    sans = [x509.DNSName("image-magic.local"), x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(priv.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
         .not_valid_after(now + dt.timedelta(days=3650))
         .add_extension(x509.SubjectAlternativeName(sans), critical=False)
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .sign(priv, hashes.SHA256()))
    cert.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    try:
        key.chmod(0o600)
    except OSError:
        pass
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    return cert, key


def _phones_file() -> Path:
    return settings_dir() / "phones.json"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Pairing:
    """The rotating pairing code and the tokens of paired phones.

    Tokens are kept hashed in phones.json (settings folder) so a family member's
    phone stays paired after the app restarts; the phone keeps the token itself.
    """

    def __init__(self, persist: bool = True) -> None:
        self._lock = threading.Lock()
        self.tokens: dict[str, dict] = {}          # sha256(token) -> {"paired": iso, "name": str}
        self._code = ""
        self._expires = 0.0
        self._tries = 0
        self._persist = persist
        if persist:
            try:
                self.tokens = {k: v for k, v in json.loads(_phones_file().read_text(encoding="utf-8")).items() if isinstance(v, dict)}
            except (OSError, ValueError):
                self.tokens = {}

    def _save(self) -> None:
        if not self._persist:
            return
        f = _phones_file()
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(self.tokens, indent=1), encoding="utf-8")
            f.chmod(0o600)
        except OSError:
            pass

    def code(self) -> tuple[str, int]:
        with self._lock:
            if not self._code or time.time() > self._expires or self._tries >= MAX_TRIES:
                self._code = f"{secrets.randbelow(1_000_000):06d}"
                self._expires = time.time() + CODE_TTL
                self._tries = 0
            return self._code, int(self._expires - time.time())

    def try_code(self, code: str, name: str = "") -> str | None:
        """A token for the phone if the code is right, else None (and one try used up)."""
        with self._lock:
            ok = bool(self._code) and time.time() <= self._expires and self._tries < MAX_TRIES \
                and secrets.compare_digest(code.strip(), self._code)
            if not ok:
                self._tries += 1
                return None
            self._code = ""                        # one phone per code
            token = secrets.token_urlsafe(24)
            self.tokens[_hash(token)] = {"paired": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "name": name.strip()[:40]}
            self._save()
            return token

    def valid(self, token: str) -> bool:
        return _hash(token) in self.tokens

    def name_of(self, token: str) -> str:
        return (self.tokens.get(_hash(token)) or {}).get("name", "")

    def phones(self) -> list[dict]:
        return [{"name": v.get("name", ""), "paired": v.get("paired", "")} for v in self.tokens.values()]

    def reset(self) -> None:
        with self._lock:
            self.tokens.clear()
            self._code = ""
            self._save()


def phone_allowed(path: str, method: str) -> bool:
    """Allowlist: a route a phone may call. Anything not listed is laptop-only."""
    if method == "GET":
        if path in PHONE_GET_EXACT_DENY or path.startswith("/api/review-queue"):
            return False
        return any(path == p or path.startswith(p) for p in PHONE_GET)
    if method == "POST":
        return path in PHONE_POST
    return False


def laptop_only(path: str, method: str = "POST") -> bool:
    return not phone_allowed(path, method)


PAIR_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pair with Image Magic</title><style>
body{margin:0;font:17px/1.5 system-ui,sans-serif;background:#0f1416;color:#e6edf0;display:grid;place-items:center;min-height:100vh}
form{background:#182024;padding:28px;border-radius:16px;width:min(360px,90vw)} h1{font-size:22px;margin:0 0 6px}
input{font-size:28px;letter-spacing:6px;text-align:center;width:100%;padding:10px;border-radius:10px;border:1px solid #2a3a40;background:#0f1416;color:#fff;margin:14px 0}
button{width:100%;padding:12px;border:0;border-radius:10px;background:#14b8a6;color:#04201c;font-weight:700;font-size:16px}
p{color:#9fb0b7;font-size:14px;margin:6px 0} .err{color:#f87171}</style></head><body>
<form method="post" action="/pair"><h1>Pair this phone</h1><p>Type the 6-digit code shown in Image Magic → Settings → Phone access on the laptop, and your name (photos you add are marked with it).</p>
<input name="name" placeholder="Your name" maxlength="40" style="font-size:18px;letter-spacing:0" required>
<input name="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" required>
<button type="submit">Connect</button>%ERR%</form></body></html>"""
