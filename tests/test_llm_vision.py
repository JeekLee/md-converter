import json
from unittest.mock import patch, MagicMock
from md_converter.llm import LlmConfig, vision_to_mermaid


def _cfg() -> LlmConfig:
    return LlmConfig(url="http://localhost:10080/v1", api_key="test", model="test-vision")


def _mock_resp(content: str):
    m = MagicMock()
    m.read.return_value = json.dumps(
        {"choices": [{"message": {"content": content}}]}
    ).encode()
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


def test_returns_mermaid():
    with patch("urllib.request.urlopen", return_value=_mock_resp("graph TD\n  A --> B")):
        result = vision_to_mermaid(b"fake-png", _cfg())
    assert result == "graph TD\n  A --> B"


def test_strips_mermaid_fences():
    with patch("urllib.request.urlopen",
               return_value=_mock_resp("```mermaid\ngraph TD\n  A --> B\n```")):
        result = vision_to_mermaid(b"fake-png", _cfg())
    assert result == "graph TD\n  A --> B"


def test_returns_none_on_exception():
    with patch("urllib.request.urlopen", side_effect=Exception("conn failed")):
        result = vision_to_mermaid(b"fake-png", _cfg())
    assert result is None


def test_sends_base64_image():
    import base64
    png = b"png-data"
    expected_b64 = base64.b64encode(png).decode()
    captured: dict = {}

    def _urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        return _mock_resp("graph TD\n  A --> B")

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        vision_to_mermaid(png, _cfg())

    content = captured["body"]["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert expected_b64 in content[0]["image_url"]["url"]
