import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.api_core.exceptions import GoogleAPIError

from app.main import app


class AskEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_ask_returns_source_chunks(self) -> None:
        answer = {
            "answer": "Syarat pendaftaran tersedia.",
            "sources": [{"file": "panduan.pdf", "page": 12}],
            "chunks": [
                {
                    "file": "panduan.pdf",
                    "page": 12,
                    "content": "Syarat pendaftaran.",
                    "relevance_score": 0.8,
                }
            ],
        }
        with (
            patch("app.main.index_is_ready", return_value=True),
            patch("app.main.answer_question", return_value=answer),
        ):
            response = self.client.post("/ask", json={"question": "Apa syaratnya?"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sources"][0]["page"], 12)
        self.assertEqual(response.json()["chunks"][0]["content"], "Syarat pendaftaran.")

    def test_ask_exposes_gemini_error(self) -> None:
        with (
            patch("app.main.index_is_ready", return_value=True),
            patch(
                "app.main.answer_question",
                side_effect=GoogleAPIError("API unavailable"),
            ),
        ):
            response = self.client.post("/ask", json={"question": "Apa syaratnya?"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("Gemini gagal", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
