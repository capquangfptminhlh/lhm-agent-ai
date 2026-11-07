"""P4/G6 browser chrome UIA priority: bound scan, no entered-text exposure.

These are offline helper tests; native Edge is exercised by Windows CI.
"""
import unittest

import windows_native as native


class EdgeFindControlPriorityTests(unittest.TestCase):
    def test_only_browser_chrome_edit_ids_are_extra_candidates(self):
        for kind,identifier,expected in (
            ("Edit","view_1017",True),
            ("Edit","view_1045",True),
            ("Edit","view_12345678",True),
            ("Edit","view_123456789",False),
            ("Edit","search-box",False),
            ("Document","view_1045",False),
            ("Password","view_1045",False),
            ("ComboBox","view_1045",False),
            ("Edit","view_1045\\nsecret",False),
        ):
            with self.subTest(kind=kind,id=identifier):
                self.assertEqual(
                    native._is_browser_chrome_edit_meta(kind,identifier),
                    expected)

    def test_find_edit_beyond_first_sixty_is_prioritized_without_values(self):
        rows=[{"type":"Text","automation_id":"row_"+str(i),
               "label":"Public static test row"} for i in range(85)]
        address={"type":"Edit","automation_id":"view_1017",
                 "label":"","center":{"x":360,"y":70}}
        find={"type":"Edit","automation_id":"view_1045",
              "label":"","center":{"x":850,"y":110}}
        controls=native._prioritize_browser_chrome_edits(
            rows[:20]+[address]+rows[20:]+[find])
        self.assertEqual(len(controls),60)
        self.assertIs(controls[0],address)
        self.assertIs(controls[1],find)
        self.assertEqual(controls[1]["label"],"")
        self.assertEqual(controls[1]["center"]["y"],110)
        self.assertNotIn("value",controls[1])

    def test_unnamed_edit_and_no_geometry_are_not_pinned(self):
        rows=[{"type":"Text","automation_id":str(i)}
              for i in range(75)]
        unsafe={"type":"Edit","automation_id":"view_1045","label":""}
        output=native._prioritize_browser_chrome_edits(rows+[unsafe])
        self.assertEqual(len(output),60)
        self.assertNotIn(unsafe,output)

    def test_never_exceed_sixty_controls_with_many_candidates(self):
        edits=[{"type":"Edit","automation_id":"view_"+str(1000+i),
                "label":"","center":{"x":20,"y":30}} for i in range(90)]
        output=native._prioritize_browser_chrome_edits(edits)
        self.assertEqual(len(output),60)
        self.assertEqual(output[0]["automation_id"],"view_1000")


if __name__=="__main__":
    unittest.main()
