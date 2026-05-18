"""Unit tests for challan resume attach guards (no live Siebel)."""

import unittest

from app.services.hero_dms_playwright_invoice import _normalize_attach_line_items


class TestNormalizeAttachLineItemsAllowEmpty(unittest.TestCase):
    def test_empty_list_without_allow_errors(self) -> None:
        items, err = _normalize_attach_line_items(
            full_chassis="",
            line_item_discount="",
            attach_line_items=[],
        )
        self.assertEqual(items, [])
        self.assertIsNotNone(err)

    def test_empty_list_with_allow_empty_ok(self) -> None:
        items, err = _normalize_attach_line_items(
            full_chassis="",
            line_item_discount="",
            attach_line_items=[],
            allow_empty=True,
        )
        self.assertEqual(items, [])
        self.assertIsNone(err)

    def test_none_attach_still_requires_full_chassis(self) -> None:
        items, err = _normalize_attach_line_items(
            full_chassis="",
            line_item_discount="",
            attach_line_items=None,
            allow_empty=True,
        )
        self.assertEqual(items, [])
        self.assertIn("full_chassis is empty", err or "")

    def test_single_chassis_unchanged(self) -> None:
        items, err = _normalize_attach_line_items(
            full_chassis="MBLHAW520THE03790",
            line_item_discount="1500",
            attach_line_items=None,
        )
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["full_chassis"], "MBLHAW520THE03790")


if __name__ == "__main__":
    unittest.main()
