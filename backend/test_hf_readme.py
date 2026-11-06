import unittest
from io import BytesIO
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from backend.hf_readme import build_readme_url, fetch_readme


class HuggingFaceReadmeTests(unittest.TestCase):
    def test_builds_url_from_model_id_or_url(self) -> None:
        expected = "https://huggingface.co/owner/model/raw/main/README.md"
        self.assertEqual(build_readme_url("owner/model"), expected)
        self.assertEqual(build_readme_url("https://huggingface.co/owner/model"), expected)

    @patch("backend.hf_readme.urlopen")
    def test_fetches_readme_from_mocked_response(self, mock_urlopen) -> None:
        mock_urlopen.return_value = BytesIO(b"# Model card\n\nA description.")

        readme = fetch_readme("owner/model")

        self.assertEqual(readme, "# Model card\n\nA description.")
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, build_readme_url("owner/model"))

    @patch("backend.hf_readme.urlopen")
    def test_translates_http_error(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = HTTPError("https://huggingface.co/owner/model/raw/main/README.md", 404, "Not Found", {}, None)

        with self.assertRaisesRegex(ValueError, "HTTP 오류"):
            fetch_readme("owner/model")

    @patch("backend.hf_readme.urlopen")
    def test_translates_network_error(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = URLError("offline")

        with self.assertRaisesRegex(ValueError, "연결"):
            fetch_readme("owner/model")

    @patch("backend.hf_readme.urlopen")
    def test_translates_invalid_utf8(self, mock_urlopen) -> None:
        mock_urlopen.return_value = BytesIO(b"\xff")

        with self.assertRaisesRegex(ValueError, "UTF-8"):
            fetch_readme("owner/model")

    @patch("backend.hf_readme.urlopen")
    def test_rejects_empty_readme(self, mock_urlopen) -> None:
        mock_urlopen.return_value = BytesIO(b" \n\t")

        with self.assertRaisesRegex(ValueError, "비어"):
            fetch_readme("owner/model")


if __name__ == "__main__":
    unittest.main()