"""환경변수로 선택하는 LLM 제공자 어댑터."""

from collections.abc import Callable
import os


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} 환경변수가 필요합니다.")
    return value


def _build_openai_call(model: str, api_key: str) -> Callable[[str], str]:
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("OpenAI Python SDK가 설치되어 있지 않습니다.") from None

    try:
        client = OpenAI(api_key=api_key)
    except Exception:
        raise RuntimeError("OpenAI 클라이언트를 초기화하지 못했습니다.") from None

    def call_llm(prompt: str) -> str:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.choices[0].message.content
        except Exception:
            raise RuntimeError("OpenAI API 호출에 실패했습니다.") from None
        if not isinstance(content, str):
            raise RuntimeError("OpenAI 응답에 텍스트가 없습니다.")
        return content

    return call_llm


def _build_anthropic_call(model: str, api_key: str) -> Callable[[str], str]:
    try:
        from anthropic import Anthropic
    except ImportError:
        raise RuntimeError("Anthropic Python SDK가 설치되어 있지 않습니다.") from None

    try:
        client = Anthropic(api_key=api_key)
    except Exception:
        raise RuntimeError("Anthropic 클라이언트를 초기화하지 못했습니다.") from None

    def call_llm(prompt: str) -> str:
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                block.text
                for block in response.content
                if getattr(block, "type", None) == "text" and isinstance(block.text, str)
            )
        except Exception:
            raise RuntimeError("Anthropic API 호출에 실패했습니다.") from None
        if not text:
            raise RuntimeError("Anthropic 응답에 텍스트가 없습니다.")
        return text

    return call_llm


def build_call_llm_from_env() -> Callable[[str], str]:
    """환경변수 설정으로 prompt를 받아 응답 문자열을 반환하는 함수를 만든다."""
    provider = _required_env("LLM_PROVIDER").lower()
    if provider not in {"openai", "anthropic"}:
        raise ValueError("지원하지 않는 LLM 제공자입니다. openai 또는 anthropic만 사용할 수 있습니다.")

    model = _required_env("LLM_MODEL")
    key_name = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
    api_key = _required_env(key_name)

    if provider == "openai":
        return _build_openai_call(model, api_key)
    return _build_anthropic_call(model, api_key)
