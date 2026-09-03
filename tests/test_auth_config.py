import ipaddress
import pytest
from app.auth.config import (
    is_allowed_email,
    is_trusted_peer,
    classify_request,
)

DOMAINS = frozenset({"indiamart.com", "intermesh.net"})
CIDRS = (ipaddress.ip_network("127.0.0.1/32"), ipaddress.ip_network("::1/128"))


class TestIsAllowedEmail:
    def test_allowed_domain_verified(self):
        assert is_allowed_email("a.b@indiamart.com", True, DOMAINS) is True

    def test_second_allowed_domain(self):
        assert is_allowed_email("x@intermesh.net", True, DOMAINS) is True

    def test_case_insensitive_domain(self):
        assert is_allowed_email("x@IndiaMart.com", True, DOMAINS) is True

    def test_unverified_rejected(self):
        # WHY: an unverified Google email can be attacker-controlled; must never pass.
        assert is_allowed_email("x@indiamart.com", False, DOMAINS) is False

    def test_wrong_domain_rejected(self):
        assert is_allowed_email("x@gmail.com", True, DOMAINS) is False

    def test_lookalike_domain_rejected(self):
        # WHY: substring/suffix tricks like "evilindiamart.com" must not match.
        assert is_allowed_email("x@evilindiamart.com", True, DOMAINS) is False

    def test_none_or_malformed_rejected(self):
        assert is_allowed_email(None, True, DOMAINS) is False
        assert is_allowed_email("noatsign", True, DOMAINS) is False


class TestIsTrustedPeer:
    def test_localhost_v4_trusted(self):
        assert is_trusted_peer("127.0.0.1", CIDRS) is True

    def test_localhost_v6_trusted(self):
        assert is_trusted_peer("::1", CIDRS) is True

    def test_external_ip_untrusted(self):
        assert is_trusted_peer("203.0.113.5", CIDRS) is False

    def test_none_untrusted(self):
        assert is_trusted_peer(None, CIDRS) is False

    def test_garbage_untrusted(self):
        assert is_trusted_peer("not-an-ip", CIDRS) is False


class TestClassifyRequest:
    def test_login_public(self):
        assert classify_request("GET", "/login") == "public"

    def test_auth_routes_public(self):
        assert classify_request("GET", "/auth/callback") == "public"

    def test_static_public(self):
        assert classify_request("GET", "/static/css/style.css") == "public"

    def test_health_public(self):
        assert classify_request("GET", "/health") == "public"

    def test_audit_post_trusted_only(self):
        assert classify_request("POST", "/audit") == "trusted_only"

    def test_audit_get_is_gated(self):
        # WHY: only the consumer's POST ingestion is exempt; nothing else on /audit.
        assert classify_request("GET", "/audit") == "gated"

    def test_admin_audit_gated(self):
        assert classify_request("POST", "/admin_view/audit") == "gated"

    def test_api_audit_gated(self):
        assert classify_request("POST", "/api/audit") == "gated"

    def test_root_gated(self):
        assert classify_request("GET", "/") == "gated"

    def test_docs_gated(self):
        assert classify_request("GET", "/docs") == "gated"

    def test_download_gated(self):
        assert classify_request("GET", "/download/audit-traces") == "gated"
