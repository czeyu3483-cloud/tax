"""Separate the display-only AI footer from the substantive spoken answer."""
import re

from src.config.product import strip_product_notices

_AI_FOOTER = re.compile(
    r'以上内容\s*由\s*(?:AI|人工智能)\s*生成[，,。.!！；;：:\s]*仅供参考'
    r'(?:[，,]\s*具体请?以[^。\n]{0,100}为准)?[。.!！\s]*$',
    re.IGNORECASE,
)


def without_ai_footer(text: str) -> str:
    """Remove only trailing product notices; retain business warnings and limits."""
    text = text.strip()
    while text:
        cleaned = _AI_FOOTER.sub('', strip_product_notices(text)).rstrip()
        if cleaned == text:
            break
        text = cleaned
    return text
