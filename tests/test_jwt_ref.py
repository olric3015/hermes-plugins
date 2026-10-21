"""jwt-ref: what an @jwt: reference expands to, and the suggestions it offers."""

import asyncio
import base64
import json
import re
import sys
import types

import pytest

NOW = 1792573200.0  # 2026-10-21 09:00:00 UTC

# The example token from jwt.io: HS256, sub/name/iat, no exp.
JWT_IO = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
          ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
          ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")


def _part(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def token(header=None, claims=None, signature=b"sig-bytes-0123456789") -> str:
    header = {"alg": "RS256", "typ": "JWT"} if header is None else header
    claims = {} if claims is None else claims
    return f"{_part(header)}.{_part(claims)}.{_part(signature) if signature else ''}"


def _section(out, title):
    """The indented lines under one heading ("Header", "Claims", "Status at ...", "Notes")."""
    lines, inside = [], False
    for line in out.splitlines():
        if not line.startswith(" "):
            inside = line.startswith(title)
            continue
        if inside:
            lines.append(line)
    return lines


def test_jwt_io_example(jwtok):
    assert jwtok.expand(JWT_IO, now=NOW).splitlines() == [
        "JWT, signed (JWS), 3 parts; the signature is NOT verified",
        "Header",
        '  alg (algorithm)  "HS256" = HMAC with SHA-256',
        '  typ (type)       "JWT"',
        "Claims",
        '  sub (subject)    "1234567890"',
        '  name             "John Doe"',
        "  iat (issued at)  1516239022 = 2018-01-18 01:30:22 UTC, about 8.8 years (3,198 days) ago",
        "Status at 2026-10-21 09:00:00 UTC",
        "  no exp claim: the token does not expire on its own",
        "Notes",
        "  - signature: 32 bytes, not checked (jwt-ref decodes; it never verifies)",
    ]


def test_expired_token_with_lifetime(jwtok):
    out = jwtok.expand(token(claims={"iat": NOW - 7500, "exp": NOW - 3900}), now=NOW)
    assert "  exp (expires)    1792569300.0 = 2026-10-21 07:55:00 UTC, 1 hour 5 minutes ago" in out
    assert _section(out, "Status") == ["  EXPIRED 1 hour 5 minutes ago", "  lifetime 1 hour (exp - iat)"]


def test_valid_token_counts_down(jwtok):
    out = jwtok.expand(token(claims={"exp": int(NOW) + 3 * 86_400 + 4 * 3600}), now=NOW)
    assert _section(out, "Status") == ["  valid for 3 days 4 hours more"]
    assert "2026-10-24 13:00:00 UTC, in 3 days 4 hours" in out


def test_exp_equal_to_now_is_expired(jwtok):
    out = jwtok.expand(token(claims={"exp": int(NOW)}), now=NOW)
    assert _section(out, "Status") == ["  EXPIRED 0 seconds ago"]


def test_not_yet_valid_and_future_iat(jwtok):
    out = jwtok.expand(token(claims={"nbf": int(NOW) + 7200, "iat": int(NOW) + 7200, "exp": int(NOW) + 9000}), now=NOW)
    assert _section(out, "Status") == [
        "  valid for 2 hours 30 minutes more",
        "  NOT VALID YET: usable in 2 hours",
        "  issued in 2 hours: iat is in the future (a clock is off, or the token was made by hand)",
        "  lifetime 30 minutes (exp - iat)",
    ]


def test_small_clock_differences_are_not_flagged(jwtok):
    out = jwtok.expand(token(claims={"nbf": int(NOW) + 60, "iat": int(NOW) + 60}), now=NOW)
    assert not any("NOT VALID YET" in line or "future" in line for line in _section(out, "Status"))


def test_milliseconds_by_mistake(jwtok):
    out = jwtok.expand(token(claims={"exp": int(NOW) * 1000, "iat": int(NOW) * 1000}), now=NOW)
    assert ("  exp (expires)    1792573200000 (looks like milliseconds; JWT times are seconds)"
            " = 2026-10-21 09:00:00 UTC") in out
    assert _section(out, "Status") == [
        "  exp looks like milliseconds; a server reading it as seconds sees a date far in the future"]


@pytest.mark.parametrize("value", ["soon", True, None, [1]])
def test_time_claims_that_are_not_numbers(jwtok, value):
    out = jwtok.expand(token(claims={"exp": value}), now=NOW)
    assert f"  exp (expires)  {json.dumps(value)}" in out
    assert _section(out, "Status") == ["  exp is not a number, so the expiry cannot be read"]


def test_dates_outside_the_calendar(jwtok):
    out = jwtok.expand(token(claims={"iat": -99_999_999_999}), now=NOW)  # just under the milliseconds line
    assert "  iat (issued at)  -99999999999 (not a date jwt-ref can show)" in out
    out = jwtok.expand(token(claims={"auth_time": 1e300}), now=NOW)
    assert "1e+300 (not a date jwt-ref can show)" in out


def test_values_keep_their_json_form_and_long_ones_are_cut(jwtok):
    claims = {"aud": ["api", "billing"], "roles": {"admin": False}, "n": 3.5, "note": "x" * 500}
    out = jwtok.expand(token(claims=claims), now=NOW)
    assert '  aud (audience)  ["api", "billing"]' in out
    assert '  roles           {"admin": false}' in out
    assert "  n               3.5" in out
    assert f'  note            "{"x" * 199}... (502 characters)' in out


def test_non_ascii_values_are_shown_as_written(jwtok):
    out = jwtok.expand(token(claims={"name": "Ayşe Öztürk"}), now=NOW)
    assert '  name  "Ayşe Öztürk"' in out


def test_claims_past_the_limit_are_counted(jwtok):
    claims = {f"c{i}": i for i in range(jwtok.MAX_CLAIMS + 5)}
    out = jwtok.expand(token(claims=claims), now=NOW)
    assert "  ... and 5 more claims" in out
    assert f"  c{jwtok.MAX_CLAIMS - 1}" in out and f"  c{jwtok.MAX_CLAIMS} " not in out


def test_empty_claims(jwtok):
    out = jwtok.expand(token(claims={}), now=NOW)
    assert "Claims (none)" in out.splitlines()


def test_alg_none_is_called_out(jwtok):
    out = jwtok.expand(token(header={"alg": "none"}, claims={"sub": "a"}, signature=b""), now=NOW)
    assert '  alg (algorithm)  "none" = no signature' in out
    assert _section(out, "Notes") == [
        '  - alg is "none": the token is unsigned, so anyone can write one; a server that accepts it accepts anything']
    out = jwtok.expand(token(header={"alg": "None"}, signature=b"x"), now=NOW)
    assert "  - yet part 3 is not empty, which an unsigned token's signature must be" in out


def test_empty_signature_and_missing_alg(jwtok):
    out = jwtok.expand(token(signature=b""), now=NOW)
    assert '  - the signature (part 3) is empty although alg is "RS256"' in out
    out = jwtok.expand(token(header={"typ": "JWT"}), now=NOW)
    assert "  - the header has no alg, which a JWS requires" in out


def test_unknown_alg_is_shown_without_a_description(jwtok):
    out = jwtok.expand(token(header={"alg": "XS999"}), now=NOW)
    assert '  alg (algorithm)  "XS999"' in out.splitlines()


def test_header_that_names_its_own_key(jwtok):
    header = {"alg": "RS256", "jku": "https://evil.example/keys.json", "jwk": {"kty": "RSA"}, "crit": ["exp"]}
    notes = _section(jwtok.expand(token(header=header), now=NOW), "Notes")
    assert "  - the header points to its own key (jku, jwk); a server must not trust a key the token names" in notes
    assert '  - crit lists header fields a server must understand or reject the token: ["exp"]' in notes


def test_duplicate_names_are_reported(jwtok):
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","alg":"none"}').rstrip(b"=").decode()
    claims = base64.urlsafe_b64encode(b'{"sub":"alice","sub":"admin"}').rstrip(b"=").decode()
    out = jwtok.expand(f"{header}.{claims}.c2ln", now=NOW)
    assert '  sub (subject)  "admin"' in out
    assert "  - names that appear twice (JSON keeps the last one, other parsers may not): header alg, sub" in out


def test_payload_that_is_not_json(jwtok):
    out = jwtok.expand(token(header={"alg": "HS256", "cty": "JWT"}, claims=b"eyJhbGciOiJub25lIn0.e30."), now=NOW)
    assert out.splitlines()[4:7] == [
        "Payload (not a JSON object, 24 bytes)",
        '  "eyJhbGciOiJub25lIn0.e30."',
        "  cty is JWT: the payload is itself a token; decode it with its own @jwt: reference",
    ]
    assert "Status" not in out
    out = jwtok.expand(token(claims=b"\xff\xfe"), now=NOW)
    assert "Payload (not a JSON object, 2 bytes)" in out
    out = jwtok.expand(token(claims=[1, 2]), now=NOW)
    assert "Payload (not a JSON object, 6 bytes)" in out


def test_encrypted_token_shows_its_header_only(jwtok):
    header = _part({"alg": "RSA-OAEP", "enc": "A256GCM", "kid": "k1"})
    out = jwtok.expand(f"{header}.a2V5.aXY.Y2lwaGVy.dGFn", now=NOW)
    assert out.splitlines() == [
        "JWT, encrypted (JWE), 5 parts",
        "Header",
        '  alg (algorithm)   "RSA-OAEP"',
        '  enc (encryption)  "A256GCM"',
        '  kid (key id)      "k1"',
        "Notes",
        "  - encrypted (JWE): the claims cannot be read without the recipient's key; only the header is shown",
    ]


def test_bearer_prefix_padding_and_spaces_are_accepted(jwtok):
    plain = jwtok.expand(JWT_IO, now=NOW)
    assert jwtok.expand(f"  Bearer {JWT_IO} ", now=NOW) == plain
    assert jwtok.expand(f"bearer\t{JWT_IO}", now=NOW) == plain
    header, claims, signature = JWT_IO.split(".")
    assert jwtok.expand(f"{header}==.{claims}.{signature}=", now=NOW) == plain


@pytest.mark.parametrize("value, message", [
    ("", "no token given"),
    ("Bearer ", "no token given"),
    ("abc", "a JWT has 3 parts (signed) or 5 parts (encrypted) separated by dots; this has 1"),
    ("a.b.c.d", "this has 4"),
    ("a.b.c.d.e.f", "this has 6"),
    ("ey$J.e30.", "the header (part 1) is not base64url"),
    ("eyJhbGciOiJIUzI1NiJ9.e3+.", "the payload (part 2) is not base64url"),
    ("eyJhbGciOiJIUzI1NiJ9.e30.abcde", "the signature (part 3) is not valid base64url: its length (5)"),
    ("aGVsbG8.e30.", "the header (part 1) decodes, but not to JSON"),
    ("WzFd.e30.", "the header (part 1) is JSON but not an object"),
    ("eyJhbGciOiJSU0EtT0FFUCJ9.a.b.c.d", "the part 2 is not valid base64url"),
])
def test_bad_values_raise_with_a_clear_message(jwtok, value, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        jwtok.expand(value, now=NOW)


def test_too_long_tokens_are_refused(jwtok):
    long_token = token(claims={"pad": "x" * jwtok.MAX_TOKEN_CHARS})
    with pytest.raises(ValueError, match="characters long; jwt-ref reads at most 16,384"):
        jwtok.expand(long_token, now=NOW)


@pytest.mark.parametrize("seconds, words", [
    (0.4, "0 seconds"),
    (1, "1 second"),
    (61, "1 minute 1 second"),
    (3600, "1 hour"),
    (90061, "1 day 1 hour"),
    (365 * 86_400, "about 1.0 years (365 days)"),
])
def test_span(jwtok, seconds, words):
    assert jwtok.span(seconds) == words


def test_now_defaults_to_the_clock(jwtok, monkeypatch):
    monkeypatch.setattr(jwtok.time, "time", lambda: NOW)
    assert "Status at 2026-10-21 09:00:00 UTC" in jwtok.expand(JWT_IO)


def test_preview(jwtok):
    live = token(claims={"sub": "user-4821", "exp": int(NOW) + 3600})
    assert jwtok.preview(live, now=NOW) == [(live, 'alg RS256, sub "user-4821", expires in 1 hour', "JWT")]
    assert jwtok.preview(f"Bearer {JWT_IO}", now=NOW) == [(JWT_IO, 'alg HS256, sub "1234567890"', "JWT")]
    dead = token(claims={"exp": int(NOW) - 60})
    assert jwtok.preview(dead, now=NOW)[0][1] == "alg RS256, expired 1 minute ago"
    jwe = f"{_part({'alg': 'dir', 'enc': 'A128GCM'})}..aXY.Y2lwaGVy.dGFn"
    assert jwtok.preview(jwe, now=NOW) == [(jwe, "alg dir, encrypted", "JWT")]
    assert jwtok.preview("", now=NOW) == []
    assert jwtok.preview("eyJhbGci", now=NOW) == []
    assert jwtok.preview("not.a.token", now=NOW) == []


@pytest.fixture
def hermes_api(monkeypatch):
    """Stand-ins for the two classes the plugin takes from agent.context_references."""
    module = types.ModuleType("agent.context_references")

    class ContextCompletionItem:
        def __init__(self, text, display="", meta=""):
            self.text, self.display, self.meta = text, display or text, meta

    class ContextReferenceProvider:
        prefix = ""
        description = ""

    module.ContextCompletionItem = ContextCompletionItem
    module.ContextReferenceProvider = ContextReferenceProvider
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.context_references", module)
    return module


def test_provider_expands_and_suggests(jwt_plugin, hermes_api):
    provider = jwt_plugin.make_provider()
    assert isinstance(provider, hermes_api.ContextReferenceProvider)
    assert provider.prefix == "jwt" and provider.description
    out = asyncio.run(provider.expand(JWT_IO))
    assert '  sub (subject)    "1234567890"' in out
    items = asyncio.run(provider.autocomplete(JWT_IO, limit=5))
    assert [(i.text, i.meta) for i in items] == [(JWT_IO, "JWT")]
    assert asyncio.run(provider.autocomplete(JWT_IO, limit=0)) == []
    with pytest.raises(ValueError, match="no token given"):
        asyncio.run(provider.expand(""))


def test_register_registers_one_context_reference(jwt_plugin, hermes_api):
    calls = []

    class Ctx:
        def register_context_reference(self, provider):
            calls.append(provider)

    jwt_plugin.register(Ctx())
    assert len(calls) == 1 and calls[0].prefix == "jwt"


def test_padding_does_not_count_toward_the_length_in_errors(jwtok):
    with pytest.raises(ValueError, match=re.escape("its length (5) cannot be decoded")):
        jwtok.expand("eyJhbGciOiJIUzI1NiJ9.e30.abcde=", now=NOW)


def test_negative_milliseconds_are_recognised(jwtok):
    out = jwtok.expand(token(claims={"iat": -int(NOW) * 1000}), now=NOW)
    assert "(looks like milliseconds; JWT times are seconds) = 1913-03-13 15:00:00 UTC" in out


def test_no_lifetime_when_exp_is_before_iat(jwtok):
    out = jwtok.expand(token(claims={"iat": int(NOW), "exp": int(NOW) - 10}), now=NOW)
    assert not any(line.strip().startswith("lifetime") for line in _section(out, "Status"))


def test_time_at_the_current_second_reads_now(jwtok):
    out = jwtok.expand(token(claims={"iat": NOW + 0.5}), now=NOW)
    assert "= 2026-10-21 09:00:00.5 UTC, now" in out


def test_claim_named_alg_gets_no_description_and_header_times_are_dated(jwtok):
    header = {"alg": "HS256", "exp": int(NOW) + 60}
    out = jwtok.expand(token(header=header, claims={"alg": "HS256"}), now=NOW)
    assert _section(out, "Claims") == ['  alg (algorithm)  "HS256"']
    assert "  exp (expires)    1792573260 = 2026-10-21 09:01:00 UTC, in 1 minute" in _section(out, "Header")


def test_preview_skips_an_exp_in_milliseconds(jwtok):
    value = token(claims={"exp": int(NOW) * 1000})
    assert jwtok.preview(value, now=NOW) == [(value, "alg RS256", "JWT")]


def test_not_before_in_milliseconds_is_not_called_future(jwtok):
    out = jwtok.expand(token(claims={"nbf": int(NOW) * 1000}), now=NOW)
    assert not any("NOT VALID YET" in line for line in _section(out, "Status"))


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_times_are_not_numbers(jwtok, literal):
    claims = base64.urlsafe_b64encode(f'{{"exp": {literal}}}'.encode()).rstrip(b"=").decode()
    out = jwtok.expand(f"{_part({'alg': 'HS256'})}.{claims}.c2ln", now=NOW)
    assert f"  exp (expires)  {literal}" in out.splitlines()
    assert _section(out, "Status") == ["  exp is not a number, so the expiry cannot be read"]


def test_unsigned_token_without_its_final_dot(jwtok):
    # Unquoted, "@jwt:<header>.<payload>." loses its last "." to Hermes's punctuation trimming.
    value = token(header={"alg": "none"}, claims={"sub": "admin"}, signature=b"")[:-1]
    out = jwtok.expand(value, now=NOW)
    assert out.splitlines()[0] == "JWT, signed (JWS), 2 parts; the signature is NOT verified"
    assert _section(out, "Notes") == [
        "  - only 2 parts: the signature part is missing (an unsigned token ends in '.', which Hermes "
        "drops as punctuation unless the reference is in quotes)",
        '  - alg is "none": the token is unsigned, so anyone can write one; a server that accepts it accepts anything',
    ]
    signed = jwtok.expand(token(signature=b"")[:-1], now=NOW)
    assert "empty although" not in signed and "only 2 parts" in signed


def test_status_time_is_whole_seconds(jwtok):
    out = jwtok.expand(JWT_IO, now=NOW + 0.703)
    assert "Status at 2026-10-21 09:00:00 UTC" in out.splitlines()
