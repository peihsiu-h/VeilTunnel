"""Self-signed TLS certificate helper."""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import logging
import os

log = logging.getLogger(__name__)


def generate_self_signed_cert(cert_path: str, key_path: str) -> None:
    """Generate a self-signed certificate if none exists."""
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as exc:
        raise RuntimeError("the 'cryptography' package is required to generate a "
                           "certificate: pip install cryptography") from exc

    log.info("Generating self-signed certificate …")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    for path in (cert_path, key_path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    log.info("Certificate saved to %s", cert_path)


def fingerprint(cert_path: str) -> str | None:
    """SHA-256 fingerprint of a PEM certificate ("AB:CD:…"), or None."""
    try:
        import ssl
        with open(cert_path, encoding="ascii") as f:
            der = ssl.PEM_cert_to_DER_cert(f.read())
    except (OSError, ValueError):
        return None
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
