from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from backend.hf_metadata import parse_model_id


_REQUEST_TIMEOUT_SECONDS = 10


def build_readme_url(model_id_or_url: str) -> str:
    model_id = parse_model_id(model_id_or_url)
    return f"https://huggingface.co/{quote(model_id, safe='/')}/raw/main/README.md"


def fetch_readme(model_id_or_url: str) -> str:
    readme_url = build_readme_url(model_id_or_url)
    request = Request(readme_url, headers={"Accept": "text/plain"})

    try:
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            payload = response.read()
    except HTTPError as error:
        raise ValueError(f"Hugging Face README HTTP 오류({error.code}): 모델 카드를 가져오지 못했습니다.") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ValueError("Hugging Face README에 연결할 수 없습니다.") from error

    try:
        readme = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Hugging Face README를 UTF-8로 해석할 수 없습니다.") from error

    if not readme.strip():
        raise ValueError("Hugging Face README가 비어 있습니다.")
    return readme