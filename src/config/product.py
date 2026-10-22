"""Product identity and user-facing conventions shared by Web and CLI."""
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
不得自称主管机关或代表主管部门作出决定；文稿标注“草稿，供审核”，不虚构发文机关、文号或落款。
尊重用户提供的原文和明确的文体要求；引用政策原文时保持原意，不以润色代替核实。
'''


def strip_product_notices(text: str) -> str:
    """Normalize current and historical product footers without rewriting records."""
    text = text.strip()
    notices = (DISCLAIMER, *LEGACY_DISCLAIMERS)
    while text:
        notice = next((item for item in notices if text.endswith(item)), None)
        if notice is None:
            break
        text = text[:-len(notice)].rstrip()
    return text
