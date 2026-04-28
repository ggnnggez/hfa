import unittest

from path_utils import normalize_path


class NormalizePathTests(unittest.TestCase):
    def test_removes_dots_and_duplicate_slashes(self):
        self.assertEqual(normalize_path("src//./pkg/module.py"), "src/pkg/module.py")

    def test_resolves_parent_segments(self):
        self.assertEqual(normalize_path("src/pkg/../tests/./test_app.py"), "src/tests/test_app.py")

    def test_preserves_leading_parent_segments(self):
        self.assertEqual(normalize_path("../src/../README.md"), "../README.md")


if __name__ == "__main__":
    unittest.main()
