"""JWT decoding for ``@jwt:``: a token's header and claims, made readable. Nothing is verified.

Standard library only (``base64``, ``json``, ``datetime``): nothing here imports Hermes, so it runs
unchanged under the admission probe and in unit tests. Every function that depends on the current
time takes ``now`` (Unix seconds) so tests can pin it.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

MAX_TOKEN_CHARS = 16_384  # a token longer than this is refused, not decoded
MAX_CLAIMS = 60           # claims listed; the rest are counted
MAX_VALUE_CHARS = 200     # one value's JSON is cut after this many characters

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DAY = 86_400
_SKEW = 60                # seconds of clock difference that are not worth a warning
_MS_LIKE = 100_000_000_000  # a NumericDate this large is milliseconds by mistake (year 5138 in seconds)

_BEARER = re.compile(r"^bearer(?:\s+|$)", re.IGNORECASE)
_PART = re.compile(r"^[A-Za-z0-9_-]*=*$")

_ALGORITHMS = {
    "HS256": "HMAC with SHA-256",
    "HS384": "HMAC with SHA-384",
    "HS512": "HMAC with SHA-512",
    "RS256": "RSA PKCS#1 v1.5 with SHA-256",
    "RS384": "RSA PKCS#1 v1.5 with SHA-384",
    "RS512": "RSA PKCS#1 v1.5 with SHA-512",
    "PS256": "RSA-PSS with SHA-256",
    "PS384": "RSA-PSS with SHA-384",
    "PS512": "RSA-PSS with SHA-512",
    "ES256": "ECDSA P-256 with SHA-256",
    "ES384": "ECDSA P-384 with SHA-384",
    "ES512": "ECDSA P-521 with SHA-512",
    "ES256K": "ECDSA secp256k1 with SHA-256",
    "EdDSA": "Edwards-curve signature (Ed25519 or Ed448)",
    "none": "no signature",
}
_NAMES = {
    # header (RFC 7515, RFC 7516)
    "alg": "algorithm", "typ": "type", "cty": "content type", "kid": "key id", "enc": "encryption",
    "zip": "compression", "jku": "key set URL", "jwk": "embedded key", "x5u": "certificate URL",
    "x5c": "certificate chain", "x5t": "certificate thumbprint", "crit": "must understand",
    # claims (RFC 7519, OpenID Connect)
    "iss": "issuer", "sub": "subject", "aud": "audience", "exp": "expires", "nbf": "not before",
    "iat": "issued at", "jti": "token id", "auth_time": "signed in", "azp": "authorized party",
    "nonce": "nonce", "sid": "session id", "scope": "scopes", "updated_at": "profile updated",
}
_TIME_CLAIMS = ("exp", "nbf", "iat", "auth_time", "updated_at")
_KEY_POINTERS = ("jku", "x5u", "jwk")


class Token:
    """One decoded token."""

    def __init__(self, raw: str, header: dict, claims: Optional[dict], payload: bytes,
                 signature: Optional[bytes], encrypted: bool, duplicates: List[str], parts: int):
        self.raw = raw
        self.header = header
        self.claims = claims          # None when the payload is not a JSON object or is encrypted
        self.payload = payload        # decoded payload bytes (empty for an encrypted token)
        self.signature = signature    # decoded signature bytes (None for an encrypted token)
        self.encrypted = encrypted
        self.duplicates = duplicates  # names that appear twice in the header or the claims
        self.parts = parts            # 2 when the signature part (and its dot) is missing


def _b64(part: str, what: str) -> bytes:
    if not _PART.match(part):
        raise ValueError(f"the {what} is not base64url (only A-Z, a-z, 0-9, '-' and '_' belong in a JWT)")
    text = part.rstrip("=")  # padding is optional in base64url; put back exactly what is needed
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise ValueError(f"the {what} is not valid base64url: its length ({len(text)}) cannot be decoded")


def _json(data: bytes, duplicates: List[str], prefix: str) -> Any:
    def keep_pairs(pairs):
        seen = {}
        for key, value in pairs:
            if key in seen and f"{prefix}{key}" not in duplicates:
                duplicates.append(f"{prefix}{key}")
            seen[key] = value
        return seen

    return json.loads(data.decode("utf-8"), object_pairs_hook=keep_pairs)


def clean(target: str) -> str:
    """The token in a reference value: surrounding space and a ``Bearer`` prefix removed."""
    return _BEARER.sub("", target.strip()).strip()


def decode(target: str) -> Token:
    """Decode a compact JWS (3 parts) or JWE (5 parts). Raises ValueError with the reason."""
    raw = clean(target)
    if not raw:
        raise ValueError("no token given: write @jwt:<header>.<payload>.<signature>")
    if len(raw) > MAX_TOKEN_CHARS:
        raise ValueError(f"the token is {len(raw):,} characters long; jwt-ref reads at most {MAX_TOKEN_CHARS:,}")
    parts = raw.split(".")
    # Two parts: an unsigned token ("header.payload.") whose final dot Hermes took for punctuation.
    if len(parts) not in (2, 3, 5):
        raise ValueError(f"a JWT has 3 parts (signed) or 5 parts (encrypted) separated by dots; this has {len(parts)}")
    duplicates: List[str] = []
    try:
        header = _json(_b64(parts[0], "header (part 1)"), duplicates, "header ")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("the header (part 1) decodes, but not to JSON, so this is not a JWT")
    if not isinstance(header, dict):
        raise ValueError("the header (part 1) is JSON but not an object, so this is not a JWT")
    if len(parts) == 5:
        for number, part in enumerate(parts[1:], start=2):
            _b64(part, f"part {number}")
        return Token(raw, header, None, b"", None, True, duplicates, 5)
    payload = _b64(parts[1], "payload (part 2)")
    signature = _b64(parts[2], "signature (part 3)") if len(parts) == 3 else b""
    claims = None
    try:
        decoded = _json(payload, duplicates, "")
        if isinstance(decoded, dict):
            claims = decoded
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    return Token(raw, header, claims, payload, signature, False, duplicates, len(parts))


def _clock(moment: datetime) -> str:
    stamp = moment.strftime("%Y-%m-%d %H:%M:%S")
    if moment.microsecond:
        stamp += f".{moment.microsecond:06d}".rstrip("0")
    return stamp + " UTC"


def span(seconds: float) -> str:
    """A duration in words, two units at most: '3 days 4 hours', '2 minutes 5 seconds'."""
    seconds = abs(seconds)
    if seconds < 1:
        return "0 seconds"
    whole = int(seconds)
    if whole >= 365 * _DAY:
        return f"about {whole / (365.2425 * _DAY):.1f} years ({whole // _DAY:,} days)"
    parts = []
    for size, label in ((_DAY, "day"), (3600, "hour"), (60, "minute"), (1, "second")):
        count, whole = divmod(whole, size)
        if count or parts:
            parts.append(f"{count} {label}{'' if count == 1 else 's'}")
        if len(parts) == 2:
            break
    if parts and parts[-1].startswith("0 "):
        parts.pop()
    return " ".join(parts)


def _relative(seconds: float, now: float) -> str:
    delta = seconds - now
    if abs(delta) < 1:
        return "now"
    return f"in {span(delta)}" if delta > 0 else f"{span(delta)} ago"


def _seconds(value: Any) -> Optional[float]:
    """A NumericDate as seconds, or None when the value is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return float(value)


def _moment(seconds: float) -> Optional[datetime]:
    try:
        return EPOCH + timedelta(seconds=seconds)
    except (OverflowError, ValueError):
        return None


def _value(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    if len(text) > MAX_VALUE_CHARS:
        text = text[:MAX_VALUE_CHARS] + f"... ({len(text):,} characters)"
    return text


def _time_value(value: Any, now: float) -> Optional[str]:
    seconds = _seconds(value)
    if seconds is None:
        return None
    text = _value(value)
    if abs(seconds) >= _MS_LIKE:
        moment = _moment(seconds / 1000)
        if moment is None:
            return f"{text} (not a date jwt-ref can show)"
        return f"{text} (looks like milliseconds; JWT times are seconds) = {_clock(moment)}"
    moment = _moment(seconds)
    if moment is None:
        return f"{text} (not a date jwt-ref can show)"
    return f"{text} = {_clock(moment)}, {_relative(seconds, now)}"


def _rows(items: dict, now: float, header: bool) -> Tuple[List[str], int]:
    rows = []
    for key, value in list(items.items())[:MAX_CLAIMS]:
        label = f"{key} ({_NAMES[key]})" if key in _NAMES else key
        shown = _time_value(value, now) if key in _TIME_CLAIMS else None
        if key == "alg" and header and isinstance(value, str) and value in _ALGORITHMS:
            shown = f"{_value(value)} = {_ALGORITHMS[value]}"
        rows.append((label, shown if shown is not None else _value(value)))
    width = max((len(label) for label, _ in rows), default=0)
    return [f"  {label.ljust(width)}  {shown}" for label, shown in rows], max(0, len(items) - MAX_CLAIMS)


def _status(claims: dict, now: float) -> List[str]:
    lines = []
    exp, nbf, iat = (_seconds(claims.get(k)) for k in ("exp", "nbf", "iat"))
    if "exp" not in claims:
        lines.append("no exp claim: the token does not expire on its own")
    elif exp is None:
        lines.append("exp is not a number, so the expiry cannot be read")
    elif abs(exp) >= _MS_LIKE:
        lines.append("exp looks like milliseconds; a server reading it as seconds sees a date far in the future")
    elif exp <= now:
        lines.append(f"EXPIRED {span(now - exp)} ago")
    else:
        lines.append(f"valid for {span(exp - now)} more")
    if nbf is not None and abs(nbf) < _MS_LIKE and nbf > now + _SKEW:
        lines.append(f"NOT VALID YET: usable {_relative(nbf, now)}")
    if iat is not None and abs(iat) < _MS_LIKE and iat > now + _SKEW:
        lines.append(f"issued {_relative(iat, now)}: iat is in the future (a clock is off, or the token was made by hand)")
    if exp is not None and iat is not None and abs(exp) < _MS_LIKE and abs(iat) < _MS_LIKE and exp >= iat:
        lines.append(f"lifetime {span(exp - iat)} (exp - iat)")
    return lines


def _notes(token: Token) -> List[str]:
    notes = []
    alg = token.header.get("alg")
    if token.encrypted:
        notes.append("encrypted (JWE): the claims cannot be read without the recipient's key; only the header is shown")
    else:
        if token.parts == 2:
            notes.append("only 2 parts: the signature part is missing (an unsigned token ends in '.', which Hermes "
                         "drops as punctuation unless the reference is in quotes)")
        if "alg" not in token.header:
            notes.append("the header has no alg, which a JWS requires")
        elif isinstance(alg, str) and alg.lower() == "none":
            notes.append('alg is "none": the token is unsigned, so anyone can write one; a server that accepts it accepts anything')
            if token.signature:
                notes.append("yet part 3 is not empty, which an unsigned token's signature must be")
        elif token.signature:
            notes.append(f"signature: {len(token.signature)} bytes, not checked (jwt-ref decodes; it never verifies)")
        elif token.parts == 3:
            notes.append(f"the signature (part 3) is empty although alg is {_value(alg)}")
    pointers = [k for k in _KEY_POINTERS if k in token.header]
    if pointers:
        notes.append(f"the header points to its own key ({', '.join(pointers)}); a server must not trust a key the token names")
    if "crit" in token.header:
        notes.append(f"crit lists header fields a server must understand or reject the token: {_value(token.header['crit'])}")
    if token.duplicates:
        notes.append(f"names that appear twice (JSON keeps the last one, other parsers may not): {', '.join(token.duplicates)}")
    return notes


def describe(token: Token, *, now: float) -> str:
    kind = ("encrypted (JWE), 5 parts" if token.encrypted
            else f"signed (JWS), {token.parts} parts; the signature is NOT verified")
    lines = [f"JWT, {kind}", "Header"]
    header_rows, more = _rows(token.header, now, header=True)
    lines += header_rows
    if more:
        lines.append(f"  ... and {more} more header fields")
    if not token.encrypted:
        if token.claims is None:
            preview = token.payload[:MAX_VALUE_CHARS].decode("utf-8", errors="replace")
            lines += [f"Payload (not a JSON object, {len(token.payload):,} bytes)", f"  {json.dumps(preview, ensure_ascii=False)}"]
            if str(token.header.get("cty", "")).upper() == "JWT":
                lines.append("  cty is JWT: the payload is itself a token; decode it with its own @jwt: reference")
        else:
            claim_rows, more = _rows(token.claims, now, header=False)
            lines.append("Claims" if claim_rows else "Claims (none)")
            lines += claim_rows
            if more:
                lines.append(f"  ... and {more} more claims")
            lines.append(f"Status at {_clock(_moment(int(now)) or EPOCH)}")
            lines += [f"  {line}" for line in _status(token.claims, now)]
    notes = _notes(token)
    if notes:
        lines.append("Notes")
        lines += [f"  - {note}" for note in notes]
    return "\n".join(lines)


def expand(target: str, *, now: Optional[float] = None) -> str:
    """What ``@jwt:<target>`` puts into the message."""
    now = time.time() if now is None else now
    return describe(decode(target), now=now)


def preview(query: str, *, now: Optional[float] = None) -> List[Tuple[str, str, str]]:
    """Suggestions for the TUI and desktop app: (inserted text, display, meta)."""
    now = time.time() if now is None else now
    query = clean(query)
    try:
        token = decode(query)
    except ValueError:
        return []
    bits = [f"alg {token.header.get('alg')}"]
    if token.encrypted:
        bits.append("encrypted")
    elif token.claims is not None:
        if "sub" in token.claims:
            bits.append(f"sub {_value(token.claims['sub'])[:40]}")
        exp = _seconds(token.claims.get("exp"))
        if exp is not None and abs(exp) < _MS_LIKE:
            bits.append(f"expired {span(now - exp)} ago" if exp <= now else f"expires in {span(exp - now)}")
    return [(query, ", ".join(bits), "JWT")]
