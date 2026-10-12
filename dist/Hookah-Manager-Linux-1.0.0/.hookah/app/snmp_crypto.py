"""Optional authenticated RSA/AES-GCM envelopes around complete SNMP datagrams.

RSA-OAEP/SHA-256 wraps a fresh AES-256 key for each packet. AES-GCM encrypts the
SNMP payload; RSA-PSS/SHA-256 authenticates the sender and the complete envelope.
The shared-private-key layout derives the peer public key from the configured
private PEM. An explicit peer public PEM also permits separate endpoint keys.
"""

import json
import os
import secrets
import select
import socket
import struct
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace


MAGIC = b"HKH1"
REQUEST = 1
RESPONSE = 2
ZERO_ID = b"\0" * 16
HEADER = struct.Struct("!4sBQ16s16sH12sI")
MAX_DATAGRAM = 65507
MAX_AGE_SECONDS = 120
MAX_REPLAY_ENTRIES = 65536


class CryptoProtocolError(ValueError):
    """Invalid, unauthenticated, stale, or replayed encrypted traffic."""


@lru_cache(maxsize=1)
def crypto_backend():
    try:
        from cryptography.exceptions import InvalidSignature, InvalidTag
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise RuntimeError("RSA encryption requires cryptography; install requirements-agent.txt.") from exc
    return SimpleNamespace(hashes=hashes, serialization=serialization, padding=padding, rsa=rsa,
                           AESGCM=AESGCM, InvalidSignature=InvalidSignature, InvalidTag=InvalidTag)


@dataclass(frozen=True)
class EncryptionSettings:
    enabled: bool = False
    private_key_file: Path | None = None
    peer_public_key_file: Path | None = None

    @classmethod
    def from_document(cls, document, directory=None):
        if not isinstance(document, dict):
            raise ValueError("encryption must be a JSON object.")
        unknown = set(document) - {"enabled", "private_key_file", "peer_public_key_file"}
        if unknown:
            raise ValueError("Unknown encryption fields: " + ", ".join(sorted(unknown)))
        enabled = document.get("enabled", False)
        if type(enabled) is not bool:
            raise ValueError("encryption.enabled must be true or false.")
        directory = Path(directory or Path.cwd())
        paths = {}
        for name in ("private_key_file", "peer_public_key_file"):
            value = document.get(name)
            if value is None:
                paths[name] = None
                continue
            if not isinstance(value, str) or not value:
                raise ValueError(f"encryption.{name} must be a nonempty PEM path or null.")
            path = Path(value).expanduser()
            paths[name] = (path if path.is_absolute() else directory / path).resolve()
        if enabled and paths["private_key_file"] is None:
            raise ValueError("Encryption requires an explicitly configured private_key_file.")
        return cls(enabled=enabled, **paths)

    def document(self):
        return {
            "enabled": self.enabled,
            "private_key_file": str(self.private_key_file) if self.private_key_file else None,
            "peer_public_key_file": str(self.peer_public_key_file) if self.peer_public_key_file else None,
        }


def load_encryption_settings(path):
    path = Path(path).expanduser().resolve()
    with path.open(encoding="utf-8") as source:
        document = json.load(source)
    if not isinstance(document, dict):
        raise ValueError("Encryption configuration must be a JSON object.")
    return EncryptionSettings.from_document(document.get("encryption", document), path.parent)


def generate_key_bundle(directory, bits=3072):
    """Generate the manager's key pair/configuration without overwriting files."""
    if bits not in (2048, 3072, 4096):
        raise ValueError("RSA key size must be 2048, 3072, or 4096 bits.")
    directory = Path(directory).expanduser().resolve()
    paths = {
        "private_key": directory / "manager_private.pem",
        "public_key": directory / "manager_public.pem",
        "encryption_config": directory / "manager_encryption.json",
    }
    if any(path.exists() for path in paths.values()):
        raise FileExistsError("Key/configuration files already exist in the selected directory.")
    backend = crypto_backend()
    private = backend.rsa.generate_private_key(public_exponent=65537, key_size=bits)
    serialization = backend.serialization
    data = {
        "private_key": private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()),
        "public_key": private.public_key().public_bytes(serialization.Encoding.PEM,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo),
        "encryption_config": (json.dumps({
            "enabled": True, "private_key_file": "manager_private.pem", "peer_public_key_file": None,
        }, indent=2) + "\n").encode("utf-8"),
    }
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for name, path in paths.items():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                 0o644 if name == "public_key" else 0o600)
            created.append(path)
            with os.fdopen(descriptor, "wb") as destination:
                destination.write(data[name])
                destination.flush()
                os.fsync(destination.fileno())
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return paths


@dataclass(frozen=True)
class DecryptedPacket:
    payload: bytes
    message_type: int
    message_id: bytes
    correlation_id: bytes


class RsaEnvelope:
    def __init__(self, private_key, peer_public_key=None, clock=None):
        self.backend = crypto_backend()
        if not isinstance(private_key, self.backend.rsa.RSAPrivateKey) or private_key.key_size < 2048:
            raise ValueError("A PEM RSA private key of at least 2048 bits is required.")
        peer_public_key = peer_public_key or private_key.public_key()
        if not isinstance(peer_public_key, self.backend.rsa.RSAPublicKey) or peer_public_key.key_size < 2048:
            raise ValueError("Peer public key must be RSA and at least 2048 bits.")
        self.private_key = private_key
        self.peer_public_key = peer_public_key
        self.clock = clock or time.time
        self.seen = OrderedDict()
        self.lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings, clock=None):
        if not settings.enabled:
            return None
        backend = crypto_backend()
        try:
            private = backend.serialization.load_pem_private_key(settings.private_key_file.read_bytes(), password=None)
            public = None
            if settings.peer_public_key_file is not None:
                public = backend.serialization.load_pem_public_key(settings.peer_public_key_file.read_bytes())
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid RSA PEM key; configure an unencrypted PKCS8 private PEM.") from exc
        return cls(private, public, clock)

    def _oaep(self):
        return self.backend.padding.OAEP(mgf=self.backend.padding.MGF1(self.backend.hashes.SHA256()),
                                         algorithm=self.backend.hashes.SHA256(), label=None)

    def _pss(self):
        return self.backend.padding.PSS(mgf=self.backend.padding.MGF1(self.backend.hashes.SHA256()),
                                        salt_length=self.backend.padding.PSS.DIGEST_LENGTH)

    def encrypt(self, payload, message_type=REQUEST, correlation_id=ZERO_ID):
        if message_type not in (REQUEST, RESPONSE) or len(correlation_id) != 16:
            raise CryptoProtocolError("Invalid encrypted packet type or correlation ID.")
        if (message_type == REQUEST and correlation_id != ZERO_ID) or (message_type == RESPONSE and correlation_id == ZERO_ID):
            raise CryptoProtocolError("Invalid request/response correlation.")
        payload = bytes(payload)
        aes_key, nonce, message_id = secrets.token_bytes(32), secrets.token_bytes(12), secrets.token_bytes(16)
        wrapped = self.peer_public_key.encrypt(aes_key, self._oaep())
        header = HEADER.pack(MAGIC, message_type, int(self.clock() * 1000), message_id, correlation_id,
                             len(wrapped), nonce, len(payload) + 16)
        if len(header) + len(wrapped) + len(payload) + 16 + self.private_key.key_size // 8 > MAX_DATAGRAM:
            raise CryptoProtocolError("SNMP datagram is too large for the encrypted UDP envelope.")
        aad = header + wrapped
        ciphertext = self.backend.AESGCM(aes_key).encrypt(nonce, payload, aad)
        signed = aad + ciphertext
        signature = self.private_key.sign(signed, self._pss(), self.backend.hashes.SHA256())
        return signed + signature, message_id

    def decrypt(self, datagram, expected_type):
        datagram = bytes(datagram)
        signature_size = self.peer_public_key.key_size // 8
        if len(datagram) < HEADER.size + 16 + signature_size or len(datagram) > MAX_DATAGRAM:
            raise CryptoProtocolError("Invalid encrypted datagram size.")
        try:
            magic, kind, timestamp, message_id, correlation, key_size, nonce, cipher_size = HEADER.unpack_from(datagram)
        except struct.error as exc:
            raise CryptoProtocolError("Malformed encrypted header.") from exc
        if magic != MAGIC or kind != expected_type or kind not in (REQUEST, RESPONSE):
            raise CryptoProtocolError("Expected a Hookah encrypted request/response.")
        if key_size != self.private_key.key_size // 8 or cipher_size < 16:
            raise CryptoProtocolError("Invalid RSA key/ciphertext length.")
        if HEADER.size + key_size + cipher_size + signature_size != len(datagram):
            raise CryptoProtocolError("Truncated or oversized encrypted datagram.")
        if (kind == REQUEST and correlation != ZERO_ID) or (kind == RESPONSE and correlation == ZERO_ID):
            raise CryptoProtocolError("Invalid request/response correlation.")
        signed, signature = datagram[:-signature_size], datagram[-signature_size:]
        try:
            self.peer_public_key.verify(signature, signed, self._pss(), self.backend.hashes.SHA256())
        except self.backend.InvalidSignature as exc:
            raise CryptoProtocolError("Encrypted packet authentication failed.") from exc
        now = self.clock()
        if abs(now - timestamp / 1000) > MAX_AGE_SECONDS:
            raise CryptoProtocolError("Encrypted packet is outside the allowed clock window.")
        with self.lock:
            # A sender up to one window ahead can remain valid for two windows
            # after first reception. Retain its ID through that entire period.
            while self.seen and next(iter(self.seen.values())) < now - 2 * MAX_AGE_SECONDS:
                self.seen.popitem(last=False)
            if message_id in self.seen:
                raise CryptoProtocolError("Replayed encrypted packet rejected.")
            if len(self.seen) >= MAX_REPLAY_ENTRIES:
                raise CryptoProtocolError("Encrypted replay window is full; retry after expiry.")
            wrapped = datagram[HEADER.size:HEADER.size + key_size]
            aad = datagram[:HEADER.size + key_size]
            ciphertext = datagram[HEADER.size + key_size:-signature_size]
            try:
                aes_key = self.private_key.decrypt(wrapped, self._oaep())
                if len(aes_key) != 32:
                    raise ValueError("Expected an AES-256 session key.")
                payload = self.backend.AESGCM(aes_key).decrypt(nonce, ciphertext, aad)
            except (ValueError, self.backend.InvalidTag) as exc:
                raise CryptoProtocolError("Encrypted payload could not be decrypted.") from exc
            # Keep IDs for a full receiver-side window, including skewed clocks.
            self.seen[message_id] = now
        return DecryptedPacket(payload, kind, message_id, correlation)


def parse_udp_target(target):
    value = str(target).removeprefix("udp:")
    parts = value.split(":")
    if len(parts) > 2 or not parts[0]:
        raise ValueError("Use an IPv4/hostname target, optionally udp:host:port.")
    port = int(parts[1]) if len(parts) == 2 else 161
    if not 1 <= port <= 65535:
        raise ValueError("UDP port must be between 1 and 65535.")
    return socket.gethostbyname(parts[0]), port


def snmp_request_id(payload):
    """Extract an SNMPv1/v2c PDU ID to correlate concurrent encrypted replies."""
    from pyasn1.codec.ber import decoder
    from pysnmp.proto import api
    from pysnmp.proto.api.verdec import decode_message_version

    try:
        model = api.PROTOCOL_MODULES[int(decode_message_version(payload))]
        message, remaining = decoder.decode(payload, asn1Spec=model.Message())
        if remaining:
            raise ValueError("Trailing SNMP data.")
        return int(model.apiPDU.get_request_id(model.apiMessage.get_pdu(message)))
    except Exception as exc:
        raise CryptoProtocolError("Invalid SNMPv1/v2c datagram.") from exc


class EncryptedSnmpProxy:
    """Loopback adapter for existing Net-SNMP/PySNMP clients; LAN traffic is encrypted."""

    def __init__(self, target, cipher):
        self.target = parse_udp_target(target)
        self.cipher = cipher
        self.local = None
        self.remote = None
        self.thread = None
        self.stop = threading.Event()
        self.pending = {}
        self.last_error = None

    @property
    def address(self):
        return self.local.getsockname()

    @property
    def snmp_target(self):
        host, port = self.address
        return f"udp:{host}:{port}"

    def __enter__(self):
        try:
            self.local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.local.bind(("127.0.0.1", 0))
            self.remote = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.remote.connect(self.target)
            self.thread = threading.Thread(target=self._forward, name="hookah-encrypted-snmp", daemon=True)
            self.thread.start()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def _forward(self):
        while not self.stop.is_set():
            try:
                ready, _, _ = select.select([self.local, self.remote], [], [], 0.05)
                for source in ready:
                    packet, address = source.recvfrom(MAX_DATAGRAM)
                    if source is self.local:
                        request_id = snmp_request_id(packet)
                        envelope, message_id = self.cipher.encrypt(packet)
                        now = time.monotonic()
                        self.pending = {key: value for key, value in self.pending.items() if value[2] > now - MAX_AGE_SECONDS}
                        if len(self.pending) >= 4096:
                            raise CryptoProtocolError("Too many outstanding encrypted requests.")
                        self.pending[message_id] = address, request_id, now
                        self.remote.send(envelope)
                    else:
                        response = self.cipher.decrypt(packet, RESPONSE)
                        pending = self.pending.get(response.correlation_id)
                        if pending is None or snmp_request_id(response.payload) != pending[1]:
                            raise CryptoProtocolError("Encrypted response does not match an outstanding SNMP request.")
                        self.pending.pop(response.correlation_id)
                        self.local.sendto(response.payload, pending[0])
            except (OSError, ValueError) as exc:
                if not self.stop.is_set():
                    self.last_error = str(exc)

    def __exit__(self, *exception):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        for transport in (self.local, self.remote):
            if transport is not None:
                transport.close()
