"""Extract only an unambiguous percentage shown by an official upload widget."""
import re

_PERCENT = re.compile(r"(?<!\d)(\d{1,3})\s*%(?!\d)")


def visible_percent(text: str | None) -> int | None:
    if not isinstance(text, str):
        return None
    values = {int(value) for value in _PERCENT.findall(text)}
    if len(values) != 1:
        return None
    value = values.pop()
    return value if value <= 100 else None


async def read_widget_percent(page, selector: str) -> int | None:
    """Missing or changing widgets keep the caller in indeterminate mode."""
    try:
        widgets = page.locator(selector + ":visible")
        if await widgets.count() != 1:
            return None
        return visible_percent(await widgets.inner_text(timeout=1200))
    except Exception:
        return None
