import unittest

from collections_utils import unique_stable


class UniqueStableTests(unittest.TestCase):
    def test_preserves_first_seen_order(self):
        self.assertEqual(unique_stable(["b", "a", "b", "c", "a"]), ["b", "a", "c"])

    def test_handles_numbers(self):
        self.assertEqual(unique_stable([3, 1, 3, 2, 1]), [3, 1, 2])

    def test_empty(self):
        self.assertEqual(unique_stable([]), [])


if __name__ == "__main__":
    unittest.main()
