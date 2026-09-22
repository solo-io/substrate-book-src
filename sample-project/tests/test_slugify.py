import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from slugify import slugify


class SlugifyTest(unittest.TestCase):
    def test_simple_title(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_collapses_whitespace_and_punctuation(self):
        self.assertEqual(slugify("Agent   Substrate: The Book"), "agent-substrate-the-book")

    def test_trims_punctuation(self):
        self.assertEqual(slugify("---Ready?---"), "ready")

    def test_empty_slug(self):
        self.assertEqual(slugify("!!!"), "")


if __name__ == "__main__":
    unittest.main()

