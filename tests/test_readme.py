"""README の「ほかの無料の検査道具との違い」が、実際にある検査コードだけを挙げていることを確かめる"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ReadmeDifferenceTest(unittest.TestCase):
    def test_codes_in_difference_section_exist(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        start = readme.index("## How this differs from other free invoice checkers")
        end = readme.index("## Error codes", start)
        codes = set(re.findall(r"`(E_[A-Z_]+)`", readme[start:end]))
        self.assertTrue(codes)
        src = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "jpinvoice").glob("*.py"))
        for code in codes:
            self.assertIn(f'"{code}"', src, code)

    def test_japanese_part_has_difference(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("ほかの無料の検査道具との違い", readme)


if __name__ == "__main__":
    unittest.main()
