import unittest

from execution.core.registry import Registry


class RegistryDiscovery(unittest.TestCase):
    def test_discovers_valid_tools_only(self):
        reg = Registry(package="tests.fixture_skills")
        names = {t.name for t in reg.all()}
        self.assertIn("fx_read", names)
        self.assertIn("fx_write", names)
        # incomplete module is silently skipped (Invariant I-1)
        self.assertNotIn("fx_incomplete", names)

    def test_production_skills_discover(self):
        reg = Registry()  # execution.skills
        names = {t.name for t in reg.all()}
        self.assertIn("system_health", names)
        self.assertIn("echo_note", names)

    def test_categories_and_schema(self):
        reg = Registry(package="tests.fixture_skills")
        write = reg.get("fx_write")
        self.assertTrue(write.is_write)
        self.assertTrue(write.requires_approval)
        schema = reg.get("fx_read").to_schema()
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "fx_read")


if __name__ == "__main__":
    unittest.main()
