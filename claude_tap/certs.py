"""CA and per-host certificate generation for forward proxy TLS termination.

Generates a self-signed CA on first run and creates per-host certificates
signed by that CA. The CA cert/key are persisted to disk so they survive
restarts; host certs are cached in memory for the lifetime of the process.
"""

from __future__ import annotations

import datetime
import ipaddress
import logging
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

log = logging.getLogger("claude-tap")

# Default directory for CA files
_DEFAULT_CA_DIR = Path.home() / ".claude-tap"

# CA validity: 5 years
_CA_VALIDITY_DAYS = 5 * 365
# Host cert validity: 1 year
_HOST_VALIDITY_DAYS = 365


def _generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def ensure_ca(ca_dir: Path | None = None) -> tuple[Path, Path]:
    """Ensure a CA certificate and key exist on disk.

    Returns (ca_cert_path, ca_key_path). Creates them if they don't exist.
    """
    ca_dir = ca_dir or _DEFAULT_CA_DIR
    ca_dir.mkdir(parents=True, exist_ok=True)

    ca_cert_path = ca_dir / "ca.pem"
    ca_key_path = ca_dir / "ca-key.pem"

    if ca_cert_path.exists() and ca_key_path.exists():
        # Validate existing files are loadable
        try:
            _load_ca(ca_cert_path, ca_key_path)
            return ca_cert_path, ca_key_path
        except Exception:
            log.warning("Existing CA files are invalid, regenerating")

    log.info(f"Generating new CA certificate in {ca_dir}")
    key = _generate_key()
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "claude-tap CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "claude-tap"),
        ]
    )

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=_CA_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    ca_key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    # Restrict key file permissions
    ca_key_path.chmod(0o600)

    ca_cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    log.info(f"CA certificate written to {ca_cert_path}")
    return ca_cert_path, ca_key_path


def _load_ca(ca_cert_path: Path, ca_key_path: Path) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """Load CA cert and key from PEM files."""
    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
    return ca_cert, ca_key  # type: ignore[return-value]


class CertificateAuthority:
    """In-memory CA that generates per-host TLS certificates.

    Caches generated host certs for the lifetime of the process.
    """

    def __init__(self, ca_cert_path: Path, ca_key_path: Path) -> None:
        self._ca_cert, self._ca_key = _load_ca(ca_cert_path, ca_key_path)
        self._host_cache: dict[str, tuple[bytes, bytes]] = {}

    def get_host_cert_pem(self, hostname: str) -> tuple[bytes, bytes]:
        """Return (cert_pem, key_pem) for the given hostname.

        Generates and caches a new certificate signed by the CA if needed.
        """
        if hostname in self._host_cache:
            return self._host_cache[hostname]

        key = _generate_key()
        now = datetime.datetime.now(datetime.timezone.utc)

        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, hostname),
            ]
        )

        # Build SAN: use IPAddress for IP addresses, DNSName for hostnames
        san_names: list[x509.GeneralName] = []
        try:
            ip = ipaddress.ip_address(hostname)
            san_names.append(x509.IPAddress(ip))
        except ValueError:
            san_names.append(x509.DNSName(hostname))

        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self._ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=_HOST_VALIDITY_DAYS))
            .add_extension(
                x509.SubjectAlternativeName(san_names),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self._ca_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                critical=False,
            )
        )

        cert = builder.sign(self._ca_key, hashes.SHA256())

        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )

        self._host_cache[hostname] = (cert_pem, key_pem)
        return cert_pem, key_pem

    def make_ssl_context(self, hostname: str) -> ssl.SSLContext:
        """Create an SSL context for serving TLS as the given hostname."""
        import tempfile

        cert_pem, key_pem = self.get_host_cert_pem(hostname)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # Write cert and key to temp files (ssl module needs file paths)
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as cf:
            cf.write(cert_pem)
            cert_path = cf.name
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as kf:
            kf.write(key_pem)
            key_path = kf.name

        try:
            ctx.load_cert_chain(cert_path, key_path)
        finally:
            Path(cert_path).unlink(missing_ok=True)
            Path(key_path).unlink(missing_ok=True)

        return ctx
