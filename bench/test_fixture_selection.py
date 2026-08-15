"""Regression tests for explicit fixture selection in generate/validate."""

import pathlib
import tempfile
import unittest

import generate
import validate


class ExplicitFixtureSelectionTests(unittest.TestCase):
    def test_generate_refuses_missing_real_l2_without_fixture_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            out = tmp / "items.jsonl"
            with self.assertRaisesRegex(SystemExit, "pass --use-fixture explicitly"):
                generate.main([
                    "--cases", str(tmp / "missing-l2.jsonl"),
                    "--fixture", str(generate.FIXTURE_CASES),
                    "--out", str(out),
                    "--exclusions", str(tmp / "exclusions.jsonl"),
                ])
            self.assertFalse(out.exists())

    def test_validate_refuses_missing_real_l2_without_fixture_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            # Validation deliberately checks that the item file is non-empty
            # before resolving its source corpus. A minimal row reaches the
            # source-selection guard without loading the real bank.
            items = tmp / "items.jsonl"
            items.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "pass --use-fixture explicitly"):
                validate.main([
                    "--items", str(items),
                    "--cases", str(tmp / "missing-l2.jsonl"),
                    "--fixture", str(generate.FIXTURE_CASES),
                ])

    def test_explicit_fixture_generation_and_validation_still_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            items = tmp / "items.jsonl"
            exclusions = tmp / "exclusions.jsonl"
            missing_real = tmp / "missing-l2.jsonl"
            common = [
                "--cases", str(missing_real),
                "--use-fixture",
                "--fixture", str(generate.FIXTURE_CASES),
                "--panes", str(generate.FIXTURE_PANES),
            ]
            generate.main(common + [
                "--out", str(items),
                "--exclusions", str(exclusions),
            ])
            self.assertTrue(items.exists())
            self.assertGreater(items.stat().st_size, 0)
            self.assertEqual(validate.main(common + [
                "--items", str(items),
                "--exclusions", str(exclusions),
            ]), 0)


if __name__ == "__main__":
    unittest.main()
