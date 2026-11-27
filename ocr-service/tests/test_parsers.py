import unittest

from app.parsers import (
    classify_document,
    detect_grade_field,
    parse_matrix,
    parse_ocr_lines,
    parse_grade,
)


class StructuredParserTests(unittest.TestCase):
    def test_arabic_headers_and_grade_values(self):
        matrix = [
            ["كشف النقاط"],
            ["الرقم", "الاسم واللقب", "التقويم المستمر", "الفرض الأول", "الاختبار"],
            [7, "أحمد علي", 15, 14.5, 16],
        ]
        rows, header, warnings = parse_matrix(
            matrix, filename="arabic.xlsx", source_type="xlsx", source_name="Sheet1"
        )
        self.assertTrue(header["header_found"])
        self.assertEqual(header["fields"], ["cc", "f1", "ex"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["list_number"], 7)
        self.assertEqual(rows[0]["raw_name"], "أحمد علي")
        self.assertEqual(rows[0]["grades"], {"cc": 15.0, "f1": 14.5, "ex": 16.0})
        self.assertEqual(rows[0]["sources"]["f1"]["cell"], "D3")

    def test_french_number_column_does_not_match_nom(self):
        matrix = [
            ["N°", "Nom et prénom", "Contrôle continu", "Devoir 1", "Devoir 2", "Examen"],
            [12, "Nadia Benali", 14.5, 12, 13, 15],
        ]
        rows, header, warnings = parse_matrix(
            matrix, filename="fr.xlsx", source_type="xlsx", source_name="Sheet1"
        )
        self.assertEqual(header["fields"], ["cc", "f1", "f2", "ex"])
        self.assertEqual(rows[0]["list_number"], 12)
        self.assertEqual(rows[0]["raw_name"], "Nadia Benali")
        self.assertEqual(rows[0]["grades"], {"cc": 14.5, "f1": 12.0, "f2": 13.0, "ex": 15.0})

    def test_arabic_indic_digits_and_grade_range(self):
        self.assertEqual(parse_grade("١٨٫٥"), 18.5)
        self.assertEqual(parse_grade("١٨،٥"), 18.5)
        self.assertIsNone(parse_grade("21"))
        self.assertEqual(detect_grade_field("الفرض الثاني"), "f2")
        self.assertEqual(detect_grade_field("Contrôle continu"), "cc")

    def test_document_classification(self):
        result = classify_document(
            "كشف النقاط",
            [{"raw_name": "طالب", "grades": {"cc": 15}}],
            ["cc"],
            structured=True,
        )
        self.assertTrue(result["is_grade_sheet"])
        self.assertTrue(result["reasons"])


class OCRParserTests(unittest.TestCase):
    def test_multiple_grade_headers_on_one_ocr_line(self):
        lines = [
            {
                "line": 1,
                "tokens": ["رقم", "الاسم", "الفرض", "1", "الفرض", "2", "الاختبار"],
                "text": "رقم الاسم الفرض 1 الفرض 2 الاختبار",
                "confidence": 0.98,
                "bbox": [0, 0, 900, 30],
            },
            {
                "line": 2,
                "tokens": ["123", "سميرة", "15", "14", "16"],
                "text": "123 سميرة 15 14 16",
                "confidence": 0.91,
                "bbox": [0, 40, 900, 30],
            },
        ]
        rows, header, text = parse_ocr_lines(lines, filename="scan.jpg", page_number=1)
        self.assertEqual(header["fields"], ["f1", "f2", "ex"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["list_number"], 123)
        self.assertEqual(rows[0]["grades"], {"f1": 15.0, "f2": 14.0, "ex": 16.0})


if __name__ == "__main__":
    unittest.main()
