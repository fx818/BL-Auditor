import hmac
import ipaddress
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AuthSettings:
    google_client_id: str
    google_client_secret: str
    session_secret: str
    allowed_domains: frozenset
    trusted_cidrs: tuple
    redirect_base: str
    session_max_age: int
    https_only: bool
    admin_email: str
    admin_password: str


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def _parse_domains(raw: str) -> frozenset:
    return frozenset(d.strip().lower() for d in raw.split(",") if d.strip())


def _parse_cidrs(raw: str) -> tuple:
    nets = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return tuple(nets)


def load_settings() -> AuthSettings:
    redirect_base = os.environ.get("OAUTH_REDIRECT_BASE_URL", "").rstrip("/")
    return AuthSettings(
        google_client_id=_require("GOOGLE_CLIENT_ID"),
        google_client_secret=_require("GOOGLE_CLIENT_SECRET"),
        session_secret=_require("SESSION_SECRET"),
        allowed_domains=_parse_domains(
            os.environ.get("ALLOWED_EMAIL_DOMAINS", "indiamart.com,intermesh.net")
        ),
        trusted_cidrs=_parse_cidrs(
            os.environ.get("TRUSTED_INGEST_CIDRS", "127.0.0.1/32,::1/128")
        ),
        redirect_base=redirect_base,
        session_max_age=int(os.environ.get("SESSION_MAX_AGE", "604800")),
        https_only=redirect_base.startswith("https://"),
        admin_email=os.environ.get("ADMIN_EMAIL", "admin@admin.com"),
        admin_password=os.environ.get("ADMIN_PASSWORD", "admin123"),
    )


def verify_admin_password(candidate, admin_password) -> bool:
    if not candidate or not admin_password:
        return False
    return hmac.compare_digest(str(candidate), str(admin_password))


def is_allowed_email(email, verified, allowed_domains) -> bool:
    if not verified or not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].lower()
    return domain in allowed_domains


def is_trusted_peer(host, cidrs) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in cidrs)


def classify_request(method: str, path: str) -> str:
    if (
        path == "/login"
        or path == "/health"
        or path.startswith("/auth/")
        or path.startswith("/static/")
    ):
        return "public"
    if method == "POST" and path in ("/audit", "/windmill-batch/trigger-one"):
        return "trusted_only"
    return "gated"
