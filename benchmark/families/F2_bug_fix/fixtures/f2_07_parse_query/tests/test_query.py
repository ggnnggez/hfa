import unittest

from query import parse_query


class ParseQueryTests(unittest.TestCase):
    def test_decodes_percent_encoding_and_plus(self):
        self.assertEqual(parse_query("name=Jane+Doe&city=New%20York"), {"name": "Jane Doe", "city": "New York"})

    def test_ignores_empty_pairs(self):
        self.assertEqual(parse_query("a=1&&b=2&"), {"a": "1", "b": "2"})

    def test_missing_value_becomes_empty_string(self):
        self.assertEqual(parse_query("debug&empty="), {"debug": "", "empty": ""})


if __name__ == "__main__":
    unittest.main()
