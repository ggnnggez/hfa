import unittest

from text_utils import slugify


class SlugifyTests(unittest.TestCase):
    def test_removes_punctuation_and_collapses_separators(self):
        self.assertEqual(slugify(" Hello,   World!! "), "hello-world")

    def test_keeps_numbers_and_letters(self):
        self.assertEqual(slugify("Release v2.0 Notes"), "release-v2-0-notes")

    def test_empty_after_cleanup(self):
        self.assertEqual(slugify("!!!"), "")


if __name__ == "__main__":
    unittest.main()
