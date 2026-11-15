"""MiruTube's own certificate authority (decisions#own-ca), made with Python's
standard library alone: ECDSA keys on the P-256 curve, and X.509 certificates
written out in DER and PEM by this module's own code.

A device installs the authority's certificate once; from then on it trusts
every server certificate the authority signs for this computer's addresses,
which is made again whenever they change. The authority is limited by name
constraints to the private address ranges, `localhost` and `.local` names, so
it cannot vouch for any site on the internet even if its key were taken.

Files in the data folder:
  mirutube-ca.crt   the authority's certificate, what devices install
  mirutube-ca.key   its key, readable by its owner alone
  server.crt        the server certificate, for the names in server.json
  server.key        its key, readable by its owner alone
  server.json       {names, addresses, not_after} the server certificate covers
"""

import base64
import datetime
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import sys
import threading

AUTHORITY_YEARS = 20
SERVER_DAYS = 365
RENEW_BEFORE_DAYS = 30

# What the authority may vouch for: names under these, and addresses in these
# ranges (decisions#own-ca).
PERMITTED_NAMES = ("local", "localhost")
PERMITTED_NETWORKS = (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    "169.254.0.0/16", "100.64.0.0/10", "::1/128", "fc00::/7", "fe80::/10",
)


# --------------------------------------------------------------- data folder

def platform_data_dir():
    """certificates#fields#data_folder: the platform's data folder for
    MiruTube — not the cache folder, since losing the authority means
    installing a new one on every device."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
        return os.path.join(base, "MiruTube")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/MiruTube")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "mirutube")


# ------------------------------------------------------------ P-256 and ECDSA

P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
A = P - 3
B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
     0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)


def _add(p1, p2):
    """The sum of two points on the curve; None is the point at infinity."""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        slope = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (slope * slope - x1 - x2) % P
    return x3, (slope * (x1 - x3) - y1) % P


def _multiply(k, point=G):
    result = None
    while k:
        if k & 1:
            result = _add(result, point)
        point = _add(point, point)
        k >>= 1
    return result


def _public_bytes(public):
    """A public key as an uncompressed point, as X.509 and SEC1 write it."""
    return b"\x04" + public[0].to_bytes(32, "big") + public[1].to_bytes(32, "big")


def _new_key():
    """A private key and its public point."""
    d = secrets.randbelow(N - 1) + 1
    return d, _multiply(d)


def _sign(d, message):
    """An ECDSA signature with SHA-256 of `message`, as (r, s)."""
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    while True:
        k = secrets.randbelow(N - 1) + 1
        r = _multiply(k)[0] % N
        if r == 0:
            continue
        s = pow(k, -1, N) * (z + r * d) % N
        if s:
            return r, s


# ------------------------------------------------------------------- DER

def _tlv(tag, content):
    n = len(content)
    if n < 0x80:
        length = bytes([n])
    else:
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        length = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + length + content


def _seq(*items):
    return _tlv(0x30, b"".join(items))


def _int(value):
    raw = value.to_bytes(max(1, (value.bit_length() + 8) // 8), "big")  # a leading 0 keeps it positive
    return _tlv(0x02, raw)


def _oid(dotted):
    parts = [int(p) for p in dotted.split(".")]
    out = bytearray([40 * parts[0] + parts[1]])
    for part in parts[2:]:
        chunk = [part & 0x7f]
        part >>= 7
        while part:
            chunk.append(0x80 | (part & 0x7f))
            part >>= 7
        out.extend(reversed(chunk))
    return _tlv(0x06, bytes(out))


def _bits(data):
    return _tlv(0x03, b"\x00" + data)


def _octets(data):
    return _tlv(0x04, data)


def _utf8(text):
    return _tlv(0x0c, text.encode("utf-8"))


def _time(moment):
    """UTCTime up to 2049, GeneralizedTime after, as RFC 5280 asks."""
    if moment.year < 2050:
        return _tlv(0x17, moment.strftime("%y%m%d%H%M%SZ").encode("ascii"))
    return _tlv(0x18, moment.strftime("%Y%m%d%H%M%SZ").encode("ascii"))


def _explicit(number, content):
    return _tlv(0xa0 | number, content)


def _read(data, offset=0):
    """One DER element at `offset`: (tag, content, offset after it)."""
    tag, n = data[offset], data[offset + 1]
    offset += 2
    if n & 0x80:
        count = n & 0x7f
        n = int.from_bytes(data[offset:offset + count], "big")
        offset += count
    return tag, data[offset:offset + n], offset + n


def _children(content):
    items, offset = [], 0
    while offset < len(content):
        tag, inner, offset = _read(content, offset)
        items.append((tag, inner))
    return items


OID_EC_PUBLIC_KEY = "1.2.840.10045.2.1"
OID_P256 = "1.2.840.10045.3.1.7"
OID_ECDSA_SHA256 = "1.2.840.10045.4.3.2"
OID_COMMON_NAME = "2.5.4.3"
OID_ORGANIZATION = "2.5.4.10"
OID_BASIC_CONSTRAINTS = "2.5.29.19"
OID_KEY_USAGE = "2.5.29.15"
OID_EXT_KEY_USAGE = "2.5.29.37"
OID_SERVER_AUTH = "1.3.6.1.5.5.7.3.1"
OID_SUBJECT_ALT_NAME = "2.5.29.17"
OID_NAME_CONSTRAINTS = "2.5.29.30"
OID_SUBJECT_KEY_ID = "2.5.29.14"
OID_AUTHORITY_KEY_ID = "2.5.29.35"


def _name(common_name):
    def rdn(oid, text):
        return _tlv(0x31, _seq(_oid(oid), _utf8(text)))
    return _seq(rdn(OID_ORGANIZATION, "MiruTube"), rdn(OID_COMMON_NAME, common_name))


def _spki(public):
    return _seq(_seq(_oid(OID_EC_PUBLIC_KEY), _oid(OID_P256)), _bits(_public_bytes(public)))


def _key_id(public):
    return hashlib.sha1(_public_bytes(public)).digest()


def _extension(oid, value, critical=False):
    return _seq(_oid(oid), _tlv(0x01, b"\xff") if critical else b"", _octets(value))


def _general_name(name):
    """A dNSName, or an iPAddress for an address."""
    try:
        return _tlv(0x87, ipaddress.ip_address(name).packed)
    except ValueError:
        return _tlv(0x82, name.encode("ascii"))


def _certificate(subject, issuer, public, signer_key, signer_public, days, extensions):
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    not_before = now - datetime.timedelta(days=1)   # a device whose clock runs a little behind
    not_after = now + datetime.timedelta(days=days)
    serial = int.from_bytes(secrets.token_bytes(16), "big") >> 1 or 1
    algorithm = _seq(_oid(OID_ECDSA_SHA256))
    tbs = _seq(
        _explicit(0, _int(2)),                       # version 3
        _int(serial),
        algorithm,
        issuer,
        _seq(_time(not_before), _time(not_after)),
        subject,
        _spki(public),
        _explicit(3, _seq(
            _extension(OID_SUBJECT_KEY_ID, _octets(_key_id(public))),
            _extension(OID_AUTHORITY_KEY_ID, _seq(_tlv(0x80, _key_id(signer_public)))),
            *extensions)),
    )
    r, s = _sign(signer_key, tbs)
    return _seq(tbs, algorithm, _bits(_seq(_int(r), _int(s)))), not_after


def _private_key_der(d, public):
    """A key in SEC1 form, `EC PRIVATE KEY`, which ssl loads."""
    return _seq(_int(1), _octets(d.to_bytes(32, "big")),
                _explicit(0, _oid(OID_P256)), _explicit(1, _bits(_public_bytes(public))))


def _key_from_der(der):
    _, content, _ = _read(der)
    d = int.from_bytes(_children(content)[1][1], "big")
    return d, _multiply(d)


def _pem(label, der):
    body = base64.encodebytes(der).decode("ascii").replace("\n", "")
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return "-----BEGIN %s-----\n%s\n-----END %s-----\n" % (label, "\n".join(lines), label)


def _unpem(text):
    lines = [l for l in text.strip().splitlines() if not l.startswith("-----")]
    return base64.b64decode("".join(lines))


def _subject_of(cert_der):
    """The subject Name of a certificate, as DER, to be the issuer of the
    certificates it signs."""
    _, content, _ = _read(cert_der)
    _, tbs, _ = _read(content)
    fields = _children(tbs)
    tag, inner = fields[5]          # version, serial, algorithm, issuer, validity, subject
    return _tlv(tag, inner)


# ------------------------------------------------------------------ files

_lock = threading.Lock()


def _write(path, text, private=False):
    """Writes a file whole: a key readable by its owner alone."""
    temporary = path + ".new"
    if os.path.exists(temporary):
        os.remove(temporary)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(temporary, flags, 0o600 if private else 0o644), "w", encoding="ascii") as f:
        f.write(text)
    os.replace(temporary, path)


def _read_text(path):
    with open(path, encoding="ascii") as f:
        return f.read()


def authority(folder):
    """certificates#authority: the authority's key and certificate (DER) in
    `folder`, made there on the first start — valid for AUTHORITY_YEARS and
    named `MiruTube on <computer name>` — as (key, public, certificate)."""
    key_path = os.path.join(folder, "mirutube-ca.key")
    cert_path = os.path.join(folder, "mirutube-ca.crt")
    if os.path.exists(key_path) and os.path.exists(cert_path):
        d, public = _key_from_der(_unpem(_read_text(key_path)))
        return d, public, _unpem(_read_text(cert_path))
    os.makedirs(folder, exist_ok=True)
    d, public = _new_key()
    name = _name("MiruTube on %s" % (socket.gethostname().split(".")[0] or "this computer"))
    subtrees = [_seq(_general_name_constraint(n)) for n in PERMITTED_NAMES] + \
               [_seq(_tlv(0x87, _network_bytes(n))) for n in PERMITTED_NETWORKS]
    cert, _ = _certificate(
        name, name, public, d, public, AUTHORITY_YEARS * 365 + AUTHORITY_YEARS // 4,
        [_extension(OID_BASIC_CONSTRAINTS, _seq(_tlv(0x01, b"\xff"), _int(0)), critical=True),
         _extension(OID_KEY_USAGE, _tlv(0x03, b"\x01\x06"), critical=True),   # keyCertSign, cRLSign
         _extension(OID_NAME_CONSTRAINTS, _seq(_tlv(0xa0, b"".join(subtrees))), critical=True)])
    _write(key_path, _pem("EC PRIVATE KEY", _private_key_der(d, public)), private=True)
    _write(cert_path, _pem("CERTIFICATE", cert))
    for stale in ("server.crt", "server.key", "server.json"):   # signed by an authority now gone
        try:
            os.remove(os.path.join(folder, stale))
        except FileNotFoundError:
            pass
    return d, public, cert


def _general_name_constraint(name):
    return _tlv(0x82, name.encode("ascii"))


def _network_bytes(network):
    net = ipaddress.ip_network(network)
    return net.network_address.packed + net.netmask.packed


def authority_pem(folder):
    """The authority's certificate as PEM, what server#serve_certificate sends."""
    authority(folder)
    return _read_text(os.path.join(folder, "mirutube-ca.crt"))


def _permitted(name):
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        name = name.lower().rstrip(".")
        return any(name == n or name.endswith("." + n) for n in PERMITTED_NAMES)
    return any(ip in ipaddress.ip_network(n) for n in PERMITTED_NETWORKS)


def default_names(addresses=()):
    """What the server certificate names: `<computer name>.local`,
    `localhost`, `127.0.0.1` and `::1`, and the computer's `addresses` on the
    home network — only what the authority may vouch for, since one name
    outside its constraints would make a device refuse the whole
    certificate."""
    host = socket.gethostname().split(".")[0].lower()
    names = ["localhost", "127.0.0.1", "::1"]
    if host and host != "localhost" and all(c.isalnum() or c == "-" for c in host):
        names.insert(0, host + ".local")
    for address in addresses:
        if address and address not in names:
            names.append(address)
    return [n for n in names if _permitted(n)]


def server_certificate(folder, names):
    """certificates#server_certificate: the paths of the server certificate
    and its key for `names`, signed by the authority — the ones kept when
    they name the same and have more than RENEW_BEFORE_DAYS left, else new
    ones, valid for SERVER_DAYS."""
    with _lock:
        d, public, ca_cert = authority(folder)
        cert_path = os.path.join(folder, "server.crt")
        key_path = os.path.join(folder, "server.key")
        info_path = os.path.join(folder, "server.json")
        wanted = sorted(set(names))
        try:
            with open(info_path, encoding="utf-8") as f:
                info = json.load(f)
            not_after = datetime.datetime.fromisoformat(info["not_after"])
            fresh = not_after - datetime.datetime.now(datetime.timezone.utc) > \
                datetime.timedelta(days=RENEW_BEFORE_DAYS)
            if info.get("names") == wanted and fresh and os.path.exists(cert_path) and os.path.exists(key_path):
                return cert_path, key_path
        except (OSError, ValueError, KeyError, TypeError):
            pass
        sd, spublic = _new_key()
        dns_names = [n for n in wanted if not _is_address(n)]
        common = next((n for n in dns_names if n.endswith(".local")), "localhost")
        cert, not_after = _certificate(
            _name(common), _subject_of(ca_cert), spublic, d, public, SERVER_DAYS,
            [_extension(OID_BASIC_CONSTRAINTS, _seq(), critical=True),
             _extension(OID_KEY_USAGE, _tlv(0x03, b"\x07\x80"), critical=True),   # digitalSignature
             _extension(OID_EXT_KEY_USAGE, _seq(_oid(OID_SERVER_AUTH))),
             _extension(OID_SUBJECT_ALT_NAME, _seq(*[_general_name(n) for n in wanted]))])
        _write(key_path, _pem("EC PRIVATE KEY", _private_key_der(sd, spublic)), private=True)
        _write(cert_path, _pem("CERTIFICATE", cert))
        _write(info_path, json.dumps({"names": wanted, "not_after": not_after.isoformat()}))
        return cert_path, key_path


def _is_address(name):
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False
