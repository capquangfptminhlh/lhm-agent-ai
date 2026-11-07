"""Regression: Save As File Name must survive the 60-control UIA cap.

The production snapshot still redacts Edit values and no list-row text
beyond the first 60 is read. These tests are pure/offline.
"""
import unittest
import windows_native as native


class SaveAsControlPriorityTests(unittest.TestCase):
    def test_prioritize_filename_beyond_sixty_rows(self):
        rows=[{"type":"Edit","automation_id":"System.ItemNameDisplay",
               "label":"","center":{"x":10,"y":10}} for _ in range(75)]
        filename={"type":"Edit","automation_id":"1001",
                  "label":"","center":{"x":200,"y":300}}
        controls=native._prioritize_dialog_filename(rows+[filename])
        self.assertEqual(len(controls),60)
        self.assertIs(controls[0],filename)
        self.assertEqual(controls[0]["label"],"")
        self.assertEqual(controls[1]["automation_id"],"System.ItemNameDisplay")

    def test_combo_and_edit_match_only_known_filename_automation_ids(self):
        self.assertTrue(native._is_filename_meta("Edit","1001"))
        self.assertTrue(native._is_filename_meta("ComboBox","FileNameControlHost"))
        self.assertTrue(native._is_filename_meta("Edit","FileNameEdit"))
        self.assertFalse(native._is_filename_meta("Edit","System.ItemNameDisplay"))
        self.assertFalse(native._is_filename_meta("Button","FileNameControlHost"))
        self.assertFalse(native._is_filename_meta("Edit","search-box"))

    def test_absent_filename_does_not_invent_candidate(self):
        data=[{"type":"Edit","automation_id":"System.DateModified"} for _ in range(87)]
        controls=native._prioritize_dialog_filename(data)
        self.assertEqual(len(controls),60)
        self.assertNotIn("filename",str(controls).lower())

    def test_fixed_budget_even_if_many_file_name_controls(self):
        pinned=[{"type":"Edit","automation_id":"FileName"+str(i)}
                for i in range(80)]
        controls=native._prioritize_dialog_filename(pinned)
        self.assertEqual(len(controls),60)
        self.assertEqual(controls[0]["automation_id"],"FileName0")


if __name__=="__main__":
    unittest.main()
