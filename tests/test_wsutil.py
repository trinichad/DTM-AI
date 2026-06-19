"""WebSocket helper tests — RFC 6455 accept-key + frame encode/decode round-trip (no sockets)."""
import socket
import threading
import unittest

from execution.web import wsutil


class AcceptKey(unittest.TestCase):
    def test_rfc_vector(self):
        # RFC 6455 §1.3 worked example
        self.assertEqual(wsutil.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")


class Frames(unittest.TestCase):
    def _roundtrip(self, text: str) -> str:
        """Server-encode a frame, then have read_frame() decode it as if it were a (masked) client frame."""
        a, b = socket.socketpair()
        try:
            # masking is required of clients; mimic a client frame so read_frame unmasks it
            payload = text.encode("utf-8")
            mask = b"\x9a\x4f\x12\x7e"
            masked = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
            n = len(payload)
            hdr = bytes([0x81])
            if n < 126:
                hdr += bytes([0x80 | n])
            elif n < 65536:
                hdr += bytes([0x80 | 126]) + n.to_bytes(2, "big")
            else:
                hdr += bytes([0x80 | 127]) + n.to_bytes(8, "big")
            a.sendall(hdr + mask + masked)
            op, data = wsutil.read_frame(b)
            self.assertEqual(op, wsutil.OP_TEXT)
            return data.decode("utf-8")
        finally:
            a.close(); b.close()

    def test_short_frame(self):
        self.assertEqual(self._roundtrip("hello world"), "hello world")

    def test_medium_frame(self):
        s = "x" * 500                       # forces the 16-bit length path
        self.assertEqual(self._roundtrip(s), s)

    def test_server_encode_is_unmasked_and_fin(self):
        f = wsutil.encode_text("hi")
        self.assertEqual(f[0], 0x81)        # FIN + text opcode
        self.assertEqual(f[1], 2)           # len, no mask bit set (server→client)
        self.assertEqual(f[2:], b"hi")

    def test_close_frame_detected(self):
        a, b = socket.socketpair()
        try:
            a.sendall(bytes([0x88, 0x80]) + b"\x00\x00\x00\x00")  # masked empty CLOSE
            op, _ = wsutil.read_frame(b)
            self.assertEqual(op, wsutil.OP_CLOSE)
        finally:
            a.close(); b.close()


if __name__ == "__main__":
    unittest.main()
