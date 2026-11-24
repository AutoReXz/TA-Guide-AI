import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

import ingest


class IngestTests(unittest.TestCase):
    def test_extract_chunks_keeps_one_based_pdf_page_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "panduan.pdf"
            pdf = pymupdf.open()
            first_page = pdf.new_page()
            first_page.insert_text((72, 72), "Aturan tugas akhir halaman pertama.")
            pdf.new_page()
            third_page = pdf.new_page()
            third_page.insert_text((72, 72), "Aturan sidang halaman ketiga.")
            pdf.save(pdf_path)
            pdf.close()

            chunks = ingest.extract_chunks(pdf_path)

        self.assertEqual(
            {page for chunk in chunks for page in chunk.metadata["pages"]},
            {1, 3},
        )
        self.assertTrue(all(chunk.metadata["source"] == "panduan.pdf" for chunk in chunks))

    def test_extract_chunks_skips_page_markers_and_preserves_section_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "panduan.pdf"
            pdf = pymupdf.open()
            roman_page = pdf.new_page()
            roman_page.insert_text((72, 72), "ii")
            numbered_page = pdf.new_page()
            numbered_page.insert_text((72, 72), "4")
            content_page = pdf.new_page()
            content_page.insert_textbox(
                pymupdf.Rect(72, 72, 520, 300),
                (
                    "3.3.5. Abstrak\n"
                    "Abstrak memuat tiga bagian utama yaitu masalah penelitian, "
                    "metode penelitian, dan hasil penelitian yang dituliskan "
                    "dalam tiga paragraf.\n"
                    "a) Paragraf pertama, mencakup permasalahan penelitian.\n"
                    "b) Paragraf kedua, mencakup metode penelitian, akuisisi "
                    "data, dan metode pengembangan sistem.\n"
                    "c) Paragraf ketiga, mencakup hasil penelitian, pengujian, "
                    "jawaban permasalahan, dan kontribusi."
                ),
                fontsize=10,
            )
            pdf.save(pdf_path)
            pdf.close()

            chunks = ingest.extract_chunks(pdf_path)

        self.assertEqual(len(chunks), 1)
        chunk = chunks[0]
        self.assertEqual(chunk.metadata["page"], 3)
        self.assertEqual(chunk.metadata["section"], "3.3.5. Abstrak")
        for expected in (
            "3.3.5. Abstrak",
            "masalah penelitian",
            "metode penelitian",
            "hasil penelitian",
            "Paragraf pertama",
            "Paragraf kedua",
            "Paragraf ketiga",
        ):
            self.assertIn(expected, chunk.page_content)

    def test_extract_chunks_keeps_heading_hierarchy_and_cross_page_overlap(self) -> None:
        page_one = (
            "# BAB I PENDAHULUAN\n\n"
            "## 1.1 Latar Belakang\n\n"
            + "Isi latar belakang yang menjelaskan konteks penelitian. " * 35
        )
        page_two = (
            "Isi lanjutan yang menjelaskan konteks pada halaman berikutnya. " * 35
        )
        with patch(
            "ingest.pymupdf4llm.to_markdown",
            return_value=[
                {"text": page_one, "metadata": {"page_number": 1}},
                {"text": page_two, "metadata": {"page_number": 2}},
            ],
        ), patch("ingest.pymupdf.open") as open_pdf:
            chunks = ingest.extract_chunks(Path("panduan.pdf"))
        open_pdf.assert_called_once_with(Path("panduan.pdf"))

        self.assertGreaterEqual(len(chunks), 2)
        first, second = chunks[:2]
        self.assertEqual(
            first.metadata["section_hierarchy"],
            "BAB I PENDAHULUAN > 1.1 Latar Belakang",
        )
        self.assertEqual(first.metadata["pages"], [1])
        self.assertIn("BAB I PENDAHULUAN", second.page_content)
        self.assertIn("1.1 Latar Belakang", second.page_content)
        first_body = first.page_content.split("1.1 Latar Belakang\n\n", 1)[1]
        second_body = second.page_content.split("1.1 Latar Belakang\n\n", 1)[1]
        expected_overlap = ingest._overlap_tail(first_body)
        self.assertTrue(second_body.startswith(expected_overlap))
        self.assertGreaterEqual(len(expected_overlap), ingest.CHUNK_OVERLAP - 30)
        self.assertIn(2, {page for chunk in chunks for page in chunk.metadata["pages"]})

    def test_extract_chunks_skips_table_of_contents_pages(self) -> None:
        with patch(
            "ingest.pymupdf4llm.to_markdown",
            return_value=[
                {
                    "text": "### **DAFTAR ISI**\n\nBAB I Pendahuluan ........ 1\nBAB II Metode ........ 5",
                    "metadata": {"page_number": 1},
                },
                {
                    "text": "BAB III Hasil ........ 20\nBAB IV Penutup ........ 35",
                    "metadata": {"page_number": 2},
                },
                {
                    "text": "# BAB I PENDAHULUAN\n\nIsi bab pertama.",
                    "metadata": {"page_number": 3},
                },
            ],
        ), patch("ingest.pymupdf.open"):
            chunks = ingest.extract_chunks(Path("panduan.pdf"))

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].metadata["page"], 3)
        self.assertNotIn("DAFTAR ISI", chunks[0].page_content)

    def test_find_pdf_rejects_ambiguous_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "panduan-a.pdf").touch()
            (root / "panduan-b.pdf").touch()
            with patch.object(ingest, "ROOT_DIR", root):
                with self.assertRaisesRegex(RuntimeError, "lebih dari satu PDF"):
                    ingest.find_pdf()


if __name__ == "__main__":
    unittest.main()
