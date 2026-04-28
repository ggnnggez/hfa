import unittest

from config_parser import parse_bool


class ParseBoolTests(unittest.TestCase):
    def test_true_values(self):
        for value in ("true", "TRUE", "yes", "1", "on"):
            with self.subTest(value=value):
                self.assertIs(parse_bool(value), True)

    def test_false_values(self):
        for value in ("false", "FALSE", "no", "0", "off", ""):
            with self.subTest(value=value):
                self.assertIs(parse_bool(value), False)

    def test_rejects_unknown_string(self):
        with self.assertRaises(ValueError):
            parse_bool("sometimes")


if __name__ == "__main__":
    unittest.main()
