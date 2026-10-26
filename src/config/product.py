"""Product identity and user-facing conventions shared by Web and CLI."""
import re
from pathlib import Path

import yaml

PRODUCT = yaml.safe_load(Path(__file__).with_name('xiaochi.yaml').read_text(encoding='utf-8'))
AGENT_NAME = PRODUCT['name']
DISCLAIMER = PRODUCT['disclaimer']
LEGACY_DISCLAIMERS = (
    '以上内容由 AI 生成，仅供参考，具体请以现行有效政策及主管税务机关确认口径为准。',
)
LANGUAGE_STYLE = '''
统一使用规范、准确、简明的政务工作用语，避免口语、夸张宣传和空泛套话。
区分已知事实、政策依据、分析意见和待核实事项，不将分析意见表述为审批或审核结论。
不得自称主管机关或代表主管部门作出决定，不虚构发文机关、文号或落款。
普通对话和工作文稿不自动追加“草稿，供审核”“草稿未审核”等固定尾注；仅在用户明确要求保留或输出该文字时照录。
尊重用户提供的原文和明确的文体要求；引用政策原文时保持原意，不以润色代替核实。
'''


_DRAFT_NOTICE = re.compile(r'草稿\s*[，,、：:]?\s*(?:供审核|未审核|尚未审核|未经审核)[。.!！]?')


def requested_draft_footer(content) -> bool:
    """A narrow explicit request, not a mention or a request to remove the notice."""
    if isinstance(content, list):
        content = '\n'.join(str((item.get('text') if isinstance(item, dict)
                                 else getattr(item, 'text', '')) or '') for item in content)
    if not isinstance(content, str):
        return False
    for match in _DRAFT_NOTICE.finditer(content):
        # Only direct instructions associated with this phrase count. User
        # materials elsewhere in the turn cannot enable preservation globally.
        prefix = content[max(0, match.start()-80):match.start()]
        clause = re.split(r'[。！？!?；;\n]', prefix)[-1]
        if re.search(r'不要|无需|不必|不用|不再|别|禁止|去掉|删除|取消|移除', clause):
            continue
        if re.search(r'(?:原样(?:输出|保留|照录)?|逐字(?:输出|保留)?|一字不改|照录|保留|添加|加上|附上|写上|输出)[^。！？!?；;\n]{0,25}$', clause):
            return True
        # Exact-text requests commonly introduce a multi-line source block.
        preceding = content[:match.start()]
        if (re.search(r'(?:请|务必)[^。！？!?；;\n]{0,10}(?:原样|逐字|一字不改)[^。！？!?；;\n]{0,15}[：:]\s*\n', preceding)
                and not re.search(r'不要|无需|不必|不用|不再|禁止|去掉|删除|取消|移除', preceding)):
            return True
    return False


def _standalone_draft_notice(line: str) -> bool:
    # Indented code and quoted material are content, not product footers.
    if line.startswith(('    ', '\t')) or line.lstrip().startswith(('>', '`')):
        return False
    value = line.strip()
    for opening, closing in [('**', '**'), ('__', '__'), ('*', '*'), ('_', '_'),
                             ('（', '）'), ('(', ')')]:
        if value.startswith(opening) and value.endswith(closing):
            value = value[len(opening):-len(closing)].strip()
    return _DRAFT_NOTICE.fullmatch(value) is not None


def _trailing_notice_is_quoted(lines: list[str]) -> bool:
    fence = None
    for line in lines[:-1]:
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if marker:
            value = marker.group(1)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
    if fence:
        return True
    previous = next((line.strip() for line in reversed(lines[:-1]) if line.strip()), '')
    return re.fullmatch(r'(?:#{1,6}\s*)?(?:引用(?:原文)?|原文|摘录|示例|逐字照录)[：:]?', previous) is not None


def strip_product_notices(text: str, *, preserve_draft_footer: bool = False) -> str:
    """Remove fixed trailing notices without rewriting quoted/body wording."""
    text = text.rstrip()
    notices = (DISCLAIMER, *LEGACY_DISCLAIMERS)
    while text:
        notice = next((item for item in notices if text.endswith(item)), None)
        if notice is not None:
            text = text[:-len(notice)].rstrip()
            continue
        lines = text.splitlines()
        if (not preserve_draft_footer and lines and _standalone_draft_notice(lines[-1])
                and not _trailing_notice_is_quoted(lines)):
            text = '\n'.join(lines[:-1]).rstrip()
            continue
        break
    return text.strip()
