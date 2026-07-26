"""真实 DeepSeek V4 冒烟测试；仅显式选择 requires_model 时执行。"""
import pytest

from scdw.llm.providers.deepseek import DeepSeekProvider, FAST_MODEL


@pytest.mark.integration
@pytest.mark.requires_model
def test_v4_pro_plain_response():
    response = DeepSeekProvider().chat([{"role": "user", "content": "只回复：OK"}], thinking=False, temperature=0)
    assert response.content
    assert response.finish_reason


@pytest.mark.integration
@pytest.mark.requires_model
def test_v4_flash_plain_response():
    response = DeepSeekProvider(model=FAST_MODEL).chat([{"role": "user", "content": "只回复：OK"}], thinking=False, temperature=0)
    assert response.content


@pytest.mark.integration
@pytest.mark.requires_model
def test_v4_json_response():
    result = DeepSeekProvider().generate_json([{"role": "user", "content": "返回对象，键为 ok，值为 true"}], thinking=False)
    assert isinstance(result, dict)
