import unittest

from stats import moving_average


class MovingAverageTests(unittest.TestCase):
    def test_complete_windows(self):
        self.assertEqual(moving_average([1, 2, 3, 4], 2), [1.5, 2.5, 3.5])

    def test_window_equal_to_length(self):
        self.assertEqual(moving_average([2, 4, 6], 3), [4.0])

    def test_invalid_window(self):
        with self.assertRaises(ValueError):
            moving_average([1, 2, 3], 0)


if __name__ == "__main__":
    unittest.main()
