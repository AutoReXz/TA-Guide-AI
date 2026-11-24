import unittest
from unittest.mock import Mock, patch

from langchain_core.documents import Document

from app.rag import (
    NOT_FOUND_ANSWER,
    _get_corpus_documents,
    answer_question,
    extract_answer_text,
    hybrid_search,
    retrieve_documents,
)


class RagTests(unittest.TestCase):
    def setUp(self) -> None:
        _get_corpus_documents.cache_clear()

    def test_extract_answer_text_ignores_content_block_metadata(self) -> None:
        content = [
            {"type": "text", "text": "Jawaban pertama."},
            {"type": "text", "text": "Jawaban kedua.", "extras": {"signature": "secret"}},
        ]

        self.assertEqual(
            extract_answer_text(content),
            "Jawaban pertama.\nJawaban kedua.",
        )

    @patch("app.rag.get_llm")
    @patch("app.rag.get_vector_store")
    @patch("app.rag.MIN_RELEVANCE_SCORE", 0.45)
    def test_irrelevant_context_returns_not_found_without_calling_gemini(
        self,
        get_vector_store: Mock,
        get_llm: Mock,
    ) -> None:
        get_vector_store.return_value.similarity_search_with_relevance_scores.return_value = [
            (Document(page_content="Tidak terkait", metadata={"source": "panduan.pdf", "page": 4}), 0.1)
        ]

        result = answer_question("Berapa biaya parkir?")

        self.assertEqual(result["answer"], NOT_FOUND_ANSWER)
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["chunks"], [])
        self.assertEqual(result["text"], NOT_FOUND_ANSWER)
        get_llm.assert_not_called()

    @patch("app.rag.get_llm")
    @patch("app.rag.get_vector_store")
    def test_answer_includes_retrieved_page_source(
        self,
        get_vector_store: Mock,
        get_llm: Mock,
    ) -> None:
        get_vector_store.return_value.similarity_search_with_relevance_scores.return_value = [
            (Document(page_content="Syarat pendaftaran.", metadata={"source": "panduan.pdf", "page": 12}), 0.8)
        ]
        get_llm.return_value.invoke.return_value = Mock(
            content="Pendaftaran dilakukan sesuai syarat [hal. 12]."
        )

        result = answer_question("Apa syarat pendaftaran?")

        self.assertEqual(result["sources"], [{"file": "panduan.pdf", "page": 12}])
        chunks = result["chunks"]
        response_text = result["text"]
        assert isinstance(chunks, list)
        assert isinstance(response_text, str)
        self.assertEqual(len(chunks), 1)
        chunk = chunks[0]
        assert isinstance(chunk, dict)
        self.assertEqual(chunk["page"], 12)
        self.assertEqual(chunk["content"], "Syarat pendaftaran.")
        self.assertGreater(chunk["relevance_score"], 0)
        self.assertLessEqual(chunk["relevance_score"], 1)
        self.assertIn("Sumber: halaman 12.", response_text)

    def test_hybrid_search_recovers_exact_subsection_below_semantic_threshold(
        self,
    ) -> None:
        abstract_section = Document(
            page_content=(
                "3.3.5. Abstrak\n"
                "Abstrak memuat tiga bagian utama: masalah penelitian, metode "
                "penelitian, dan hasil penelitian dalam tiga paragraf. "
                "Paragraf pertama menjelaskan permasalahan. Paragraf kedua "
                "menjelaskan metode, akuisisi data, dan pengembangan sistem. "
                "Paragraf ketiga mencakup hasil, pengujian, jawaban "
                "permasalahan, dan kontribusi."
            ),
            metadata={"source": "panduan.pdf", "page": 46, "section": "3.3.5. Abstrak"},
        )
        semantic_distractor = Document(
            page_content="4.3.3. Kejelasan informasi dinilai dari kelengkapan.",
            metadata={"source": "panduan.pdf", "page": 139},
        )

        results = hybrid_search(
            "abstrak metode hasil penelitian",
            [(semantic_distractor, 0.7)],
            [abstract_section, semantic_distractor],
            k=10,
        )

        target_rank = next(
            rank
            for rank, (document, _) in enumerate(results, start=1)
            if document.metadata.get("section") == "3.3.5. Abstrak"
        )
        target = results[target_rank - 1][0]
        self.assertEqual(target_rank, 1)
        self.assertEqual(target.metadata["page"], 46)
        self.assertIn("kontribusi", target.page_content)
        self.assertGreaterEqual(results[0][1], 0)
        self.assertLessEqual(results[0][1], 1)

    def test_hybrid_search_returns_lexical_match_without_minimum_overlap(self) -> None:
        document = Document(
            page_content="Biaya pendaftaran dibayarkan sesuai ketentuan.",
            metadata={"source": "panduan.pdf", "page": 20},
        )

        results = hybrid_search(
            "berapa biaya parkir",
            [(document, 0.1)],
            [document],
            k=5,
        )

        self.assertEqual([item[0] for item in results], [document])

    def test_hybrid_search_boosts_section_title_containing_query_terms(self) -> None:
        section = Document(
            page_content="Penulisan bagian ini menjelaskan isi.",
            metadata={
                "source": "panduan.pdf",
                "page": 46,
                "section": "3.3.5. Penulisan Abstrak",
            },
        )

        results = hybrid_search(
            "abstrak",
            [(section, 0.8)],
            [section],
            k=1,
        )

        without_section = Document(
            page_content=section.page_content,
            metadata={"source": "panduan.pdf", "page": 46},
        )
        baseline = hybrid_search(
            "abstrak",
            [(without_section, 0.8)],
            [without_section],
            k=1,
        )
        self.assertAlmostEqual(results[0][1] - baseline[0][1], 0.35)

    @patch("app.rag.get_vector_store")
    def test_retrieve_documents_loads_corpus_once(self, get_vector_store: Mock) -> None:
        document = Document(
            page_content="Syarat pendaftaran berlaku.",
            metadata={"source": "panduan.pdf", "page": 12},
        )
        get_vector_store.return_value.similarity_search_with_relevance_scores.return_value = [
            (document, 0.8)
        ]
        get_vector_store.return_value.get.return_value = {
            "documents": [document.page_content],
            "metadatas": [document.metadata],
        }

        retrieve_documents("syarat pendaftaran")
        retrieve_documents("syarat pendaftaran")

        get_vector_store.return_value.get.assert_called_once_with(
            include=["documents", "metadatas"]
        )


if __name__ == "__main__":
    unittest.main()
