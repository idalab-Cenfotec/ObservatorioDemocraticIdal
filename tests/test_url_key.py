"""url_key debe coincidir con el índice ux_articles_url_norm de PostgreSQL."""
import unittest

from tests._util import RAIZ  # noqa: F401
from output_cleaner import url_key


class TestUrlKey(unittest.TestCase):
    def test_equivalentes(self):
        base = "x.com/nota"
        for u in ["https://www.x.com/nota/", "http://x.com/nota", "https://X.com/nota#comentarios", "https://x.com/nota//"]:
            self.assertEqual(url_key(u), base, u)

    def test_distintas(self):
        self.assertNotEqual(url_key("https://x.com/nota-1"), url_key("https://x.com/nota-2"))
        self.assertNotEqual(url_key("https://x.com/nota?id=1"), url_key("https://x.com/nota?id=2"))


if __name__ == "__main__":
    unittest.main()
