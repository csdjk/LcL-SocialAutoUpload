"""Official page percentages must be unambiguous before they reach the task UI."""
import pytest

from publishing.progress import visible_percent


@pytest.mark.parametrize(("text", "expected"), [
    ("视频上传中 42%", 42), ("100%", 100), ("上传 0 %", 0),
    ("上传 42% 处理 64%", None), ("上传 140%", None),
    ("正在上传", None), (None, None),
])
def test_visible_percent(text, expected):
    assert visible_percent(text) == expected
