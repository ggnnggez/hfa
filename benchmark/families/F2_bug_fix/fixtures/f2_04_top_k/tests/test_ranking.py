import unittest

from ranking import top_k


class TopKTests(unittest.TestCase):
    def test_returns_highest_values_descending(self):
        self.assertEqual(top_k([3, 1, 5, 2, 4], 3), [5, 4, 3])

    def test_uses_key_function(self):
        rows = [{"name": "a", "score": 2}, {"name": "b", "score": 5}, {"name": "c", "score": 3}]
        self.assertEqual([row["name"] for row in top_k(rows, 2, key=lambda row: row["score"])], ["b", "c"])

    def test_zero_k_returns_empty_list(self):
        self.assertEqual(top_k([1, 2, 3], 0), [])


if __name__ == "__main__":
    unittest.main()
