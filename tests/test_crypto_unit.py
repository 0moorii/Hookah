"""Wire encryption, packet authentication/replay protection, keys, and banner."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import snmp_agent as agent
import snmp_crypto as crypto
import snmp_data_collector_v12 as manager


class EncryptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = crypto.crypto_backend().rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_private = crypto.crypto_backend().rsa.generate_private_key(public_exponent=65537, key_size=3072)

    def endpoints(self, sender_time=1000, receiver_time=1000):
        return (crypto.RsaEnvelope(self.private, clock=lambda: sender_time),
                crypto.RsaEnvelope(self.private, clock=lambda: receiver_time))

    def test_payload_larger_than_rsa_block_encrypts_and_decrypts_both_directions(self):
        manager_side, agent_side = self.endpoints()
        payload = b"SNMP private community and OID values " * 200
        request, request_id = manager_side.encrypt(payload)
        self.assertNotIn(b"private community", request)
        decoded = agent_side.decrypt(request, crypto.REQUEST)
        self.assertEqual(decoded.payload, payload)
        self.assertEqual(decoded.message_id, request_id)
        response, _ = agent_side.encrypt(b"Counter64: 123456789", crypto.RESPONSE, request_id)
        decoded_response = manager_side.decrypt(response, crypto.RESPONSE)
        self.assertEqual(decoded_response.payload, b"Counter64: 123456789")
        self.assertEqual(decoded_response.correlation_id, request_id)

    def test_fresh_random_keys_nonces_and_ids_produce_different_ciphertexts(self):
        sender, _ = self.endpoints()
        first, first_id = sender.encrypt(b"same data")
        second, second_id = sender.encrypt(b"same data")
        self.assertNotEqual(first, second)
        self.assertNotEqual(first_id, second_id)

    def test_header_wrapped_key_payload_and_signature_tampering_are_rejected(self):
        sender, _ = self.endpoints()
        original, _ = sender.encrypt(b"SNMP data")
        for offset in (0, 10, crypto.HEADER.size + 5, crypto.HEADER.size + 256 + 2, len(original) - 1):
            with self.subTest(offset=offset):
                damaged = bytearray(original)
                damaged[offset] ^= 1
                receiver = crypto.RsaEnvelope(self.private, clock=lambda: 1000)
                with self.assertRaises(crypto.CryptoProtocolError):
                    receiver.decrypt(damaged, crypto.REQUEST)

    def test_wrong_private_key_and_untrusted_sender_are_rejected(self):
        sender, _ = self.endpoints()
        packet, _ = sender.encrypt(b"SNMP data")
        wrong_receiver = crypto.RsaEnvelope(self.other_private, clock=lambda: 1000)
        with self.assertRaises(crypto.CryptoProtocolError):
            wrong_receiver.decrypt(packet, crypto.REQUEST)

    def test_replay_and_stale_packets_are_rejected(self):
        sender, receiver = self.endpoints()
        packet, _ = sender.encrypt(b"SNMP data")
        receiver.decrypt(packet, crypto.REQUEST)
        with self.assertRaisesRegex(crypto.CryptoProtocolError, "Replayed"):
            receiver.decrypt(packet, crypto.REQUEST)
        stale_receiver = crypto.RsaEnvelope(self.private, clock=lambda: 1121)
        with self.assertRaisesRegex(crypto.CryptoProtocolError, "clock window"):
            stale_receiver.decrypt(packet, crypto.REQUEST)

    def test_replay_retention_covers_a_sender_clock_ahead(self):
        now = [1000]
        sender = crypto.RsaEnvelope(self.private, clock=lambda: 1120)
        receiver = crypto.RsaEnvelope(self.private, clock=lambda: now[0])
        packet, _ = sender.encrypt(b"SNMP data")
        receiver.decrypt(packet, crypto.REQUEST)
        now[0] = 1121
        with self.assertRaisesRegex(crypto.CryptoProtocolError, "Replayed"):
            receiver.decrypt(packet, crypto.REQUEST)

    def test_direction_plaintext_truncation_and_oversized_payload_are_rejected(self):
        sender, receiver = self.endpoints()
        packet, _ = sender.encrypt(b"SNMP data")
        for malformed, kind in ((b"ordinary SNMP bytes", crypto.REQUEST), (packet[:-1], crypto.REQUEST),
                                (packet, crypto.RESPONSE)):
            with self.subTest(kind=kind), self.assertRaises(crypto.CryptoProtocolError):
                receiver.decrypt(malformed, kind)
        with self.assertRaises(crypto.CryptoProtocolError):
            sender.encrypt(b"x" * crypto.MAX_DATAGRAM)

    def test_separate_private_keys_with_explicit_peer_public_keys_work(self):
        sender = crypto.RsaEnvelope(self.private, self.other_private.public_key(), clock=lambda: 1000)
        receiver = crypto.RsaEnvelope(self.other_private, self.private.public_key(), clock=lambda: 1000)
        packet, request_id = sender.encrypt(b"request")
        self.assertEqual(receiver.decrypt(packet, crypto.REQUEST).payload, b"request")
        response, _ = receiver.encrypt(b"response", crypto.RESPONSE, request_id)
        self.assertEqual(sender.decrypt(response, crypto.RESPONSE).payload, b"response")


class KeyAndConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hookah-keys-unit-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)

    def test_manager_key_generation_creates_matching_pems_and_relative_profile(self):
        paths = crypto.generate_key_bundle(self.path / "keys", 2048)
        settings = crypto.load_encryption_settings(paths["encryption_config"])
        cipher = crypto.RsaEnvelope.from_settings(settings)
        public = crypto.crypto_backend().serialization.load_pem_public_key(paths["public_key"].read_bytes())
        self.assertEqual(cipher.private_key.public_key().public_numbers(), public.public_numbers())
        self.assertEqual(settings.private_key_file, paths["private_key"])
        self.assertEqual(cipher.private_key.key_size, 2048)
        if os.name == "posix":
            self.assertEqual(paths["private_key"].stat().st_mode & 0o777, 0o600)
        before = paths["private_key"].read_bytes()
        with self.assertRaises(FileExistsError):
            crypto.generate_key_bundle(self.path / "keys", 2048)
        self.assertEqual(paths["private_key"].read_bytes(), before)

    def test_agent_cli_requires_explicit_private_key_and_merges_encryption_options(self):
        paths = crypto.generate_key_bundle(self.path / "keys", 2048)
        config = self.path / "agent.json"
        agent.write_json_atomic(config, agent.config_document(agent.AgentConfig()))
        with contextlib.redirect_stdout(io.StringIO()):
            agent.main(["config", "set", "--config", str(config), "--rsa-private-key", str(paths["private_key"])])
            agent.main(["config", "set", "--config", str(config), "--encryption", "on"])
        document = agent.read_config_document(config)
        settings = agent.config_from_document(document, config.parent)
        self.assertTrue(settings.encryption["enabled"])
        self.assertEqual(settings.encryption["private_key_file"], str(paths["private_key"]))
        self.assertIsNotNone(crypto.RsaEnvelope.from_settings(crypto.EncryptionSettings.from_document(settings.encryption)))
        for invalid in ({"enabled": True}, {"enabled": "true"}, {"enabled": False, "unexpected": 1}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                crypto.EncryptionSettings.from_document(invalid)

    def test_manager_key_command_prints_shisha_with_hookah_below(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = manager.main(["keys", "generate", "--directory", str(self.path / "manager"), "--bits", "2048"])
        self.assertEqual(result, 0)
        text = output.getvalue()
        self.assertIn("::::", text)
        self.assertIn("Hookah", text)
        self.assertNotIn("WELCOME", text)
        self.assertLess(text.index("::::"), text.index("Hookah"))
        self.assertIn("Made by 0moorii", text)
        self.assertTrue((self.path / "manager" / "manager_private.pem").exists())
