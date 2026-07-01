"""azure_blob (D-116) — bounded Azure Blob container reader for Content Search export.

Proves: fail-closed host allowlist (only *.blob.core.windows.net); List Blobs XML parsing incl.
NextMarker pagination; per-blob download preserves folder structure and sums bytes; a declared
total over the cap is refused before any download; blob names can't escape the dest dir.
"""
import unittest

from execution.clients import azure_blob


_CONTAINER = "https://acct.blob.core.windows.net/exportcontainer"
_SAS = "?sv=2022-11-02&ss=b&sig=abc"


def _xml(blobs, next_marker=""):
    rows = "".join(
        f"<Blob><Name>{n}</Name><Properties><Content-Length>{s}</Content-Length>"
        f"</Properties></Blob>" for n, s in blobs)
    return (f"<?xml version='1.0'?><EnumerationResults><Blobs>{rows}</Blobs>"
            f"<NextMarker>{next_marker}</NextMarker></EnumerationResults>").encode()


class HostAllowlist(unittest.TestCase):
    def test_rejects_non_azure_host(self):
        with self.assertRaises(ValueError):
            azure_blob.list_blobs("https://evil.example.com/c", _SAS)

    def test_accepts_azure_blob_host(self):
        def t(method, url):
            return 200, _xml([("a.pst", "10")])
        blobs = azure_blob.list_blobs(_CONTAINER, _SAS, transport=t)
        self.assertEqual(blobs, [{"name": "a.pst", "size": 10}])


class ListBlobs(unittest.TestCase):
    def test_follows_next_marker(self):
        pages = [_xml([("x/1.msg", "5")], next_marker="M2"), _xml([("x/2.msg", "7")])]
        seen = []

        def t(method, url):
            seen.append(url)
            return 200, pages[len(seen) - 1]
        blobs = azure_blob.list_blobs(_CONTAINER, _SAS, transport=t)
        self.assertEqual([b["name"] for b in blobs], ["x/1.msg", "x/2.msg"])
        self.assertIn("marker=M2", seen[1])               # second request carried the marker


class DownloadContainer(unittest.TestCase):
    def _lister(self, blobs):
        return lambda url, tok: [{"name": n, "size": s} for n, s in blobs]

    def test_downloads_each_blob_and_sums_bytes(self):
        got = []

        def dl(method, url, *, dest_path, max_bytes):
            got.append((url, dest_path, max_bytes))
            return 4                                       # pretend 4 bytes each
        progress = []
        man = azure_blob.download_container(
            _CONTAINER, _SAS, "/tmp/x_export", max_bytes=1000,
            on_progress=lambda i, n, name: progress.append((i, n, name)),
            lister=self._lister([("Exchange/a.msg", 4), ("Exchange/b.msg", 4)]),
            downloader=dl)
        self.assertEqual(man["blob_count"], 2)
        self.assertEqual(man["total_bytes"], 8)
        # folder structure preserved under dest dir
        self.assertTrue(man["files"][0]["path"].endswith("Exchange/a.msg"))
        self.assertEqual(progress[-1], (2, 2, "Exchange/b.msg"))

    def test_refuses_when_declared_total_over_cap(self):
        with self.assertRaises(ValueError):
            azure_blob.download_container(
                _CONTAINER, _SAS, "/tmp/x_export", max_bytes=100,
                lister=self._lister([("big.pst", 999)]),
                downloader=lambda *a, **k: 0)

    def test_blob_name_cannot_escape_dest_dir(self):
        captured = {}

        def dl(method, url, *, dest_path, max_bytes):
            captured["dest"] = dest_path
            return 1
        azure_blob.download_container(
            _CONTAINER, _SAS, "/tmp/x_export", max_bytes=1000,
            lister=self._lister([("../../etc/passwd", 1)]), downloader=dl)
        # '..' stripped — stays under the dest dir
        self.assertTrue(captured["dest"].startswith("/tmp/x_export/"))
        self.assertNotIn("..", captured["dest"])


if __name__ == "__main__":
    unittest.main()
