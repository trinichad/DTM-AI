"""Host-stats collector — must always return a well-formed dict and never raise,
even where a source is absent (no /proc on macOS, no nvidia-smi, etc.)."""
import unittest

from execution.core import sysstats


class SysStats(unittest.TestCase):
    def test_collect_shape_and_tolerance(self):
        s = sysstats.collect()
        self.assertIn("cpu", s)
        self.assertIn("memory", s)
        self.assertIn("disk", s)
        self.assertIsInstance(s["gpu"], list)          # [] when no GPU/tool, never None
        self.assertIn("cores", s["cpu"])
        # disk via shutil works on every platform we run on
        if s["disk"]:
            self.assertGreater(s["disk"]["total_gb"], 0)

    def test_num_helper(self):
        self.assertEqual(sysstats._num("42"), 42.0)
        self.assertIsNone(sysstats._num("n/a"))
        self.assertIsNone(sysstats._num(None))


if __name__ == "__main__":
    unittest.main()
