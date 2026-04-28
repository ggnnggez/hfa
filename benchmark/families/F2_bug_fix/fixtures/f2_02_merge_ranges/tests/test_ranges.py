import unittest

from ranges import merge_ranges


class MergeRangesTests(unittest.TestCase):
    def test_merges_overlapping_ranges(self):
        self.assertEqual(merge_ranges([(5, 8), (1, 3), (2, 6)]), [(1, 8)])

    def test_merges_touching_ranges(self):
        self.assertEqual(merge_ranges([(1, 3), (4, 5), (8, 9)]), [(1, 5), (8, 9)])

    def test_keeps_separate_ranges(self):
        self.assertEqual(merge_ranges([(10, 12), (1, 2), (5, 7)]), [(1, 2), (5, 7), (10, 12)])


if __name__ == "__main__":
    unittest.main()
