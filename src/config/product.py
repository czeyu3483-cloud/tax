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
采用监管机构业务咨询的答复文风：准确、克制、简短，结论在前，直接回答能否办理、是否符合规定。
已有明确适用规定且所述事实满足禁止或不符合要件时，首句直接说明“不能办理”“不符合规定”或“该行为违反……规定”，随后仅给决定性理由。
不得将明确禁止或已满足违规要件的行为弱化为“有风险”“不建议”“一般无法形成操作流程”“建议先找合规渠道”；不必以尚无处罚决定为由回避对规则及其适用的说明。
用户陈述尚未经查证时，用一次“根据所述情况”限定事实基础即可，不在每句话重复假设、免责声明或弱化已经明确的结论。
仅有疑点、材料不足或适用规则不明时，明确说“现有信息不足以判断”并指出决定性缺口；不能为迎合拒绝倾向而直接认定违法，也不能用“灰色地带”代替判断。
否定结论必须准确限定主体、行为、业务环节和适用时点，不把典型交易特征或取证线索擅自增加为违规成立的必要要件；境内购汇汇出的用途限制不能自动扩大为所有境外自有资金交易均被禁止，合法持有境外资金本身也不证明购房可办理。
已明确为规避限额而借用他人额度、分拆购付汇且适用规则禁止时，应指出违反相关规定，不仅提示风险；仅凭“多笔”“多人”或“蚂蚁搬家”标签、尚不明确规避事实时，不直接认定违规。
普通单项咨询用1至3句说清即停止，不设最低字数，正文原则上不超过150字（不含引用详情和程序追加的提示）。先给结论，再给一句直接理由；只有必要时才补条件。多种情况确需区分时至多三点，每点1至2句，不固定展开背景、风险、缺口、建议等六段式长文。
允许合规办理且条件已具备时，同样直接说明“可以按规定办理”；尚不能办理与违法、涉嫌违法与已确认违法不能混用。
只保留会改变结论的条件、例外或缺口，不重复复述案例，不展开未问事项或绕行方案，不照搬历史答复的冗长和模糊措辞。用户要求详细分析、正式报告或完整原文时再按要求展开。
需要追问时，正文至多一句、50字以内的必要说明；关键问题放入交互选项，每题通常不超过30字，不在题干堆括号例子，不重复列调查清单；遵守模式及每轮最多两问的限制。
来源、效力与适用性核验仍按当前模式执行；无法核实且影响结论的依据，用一句“依据尚待核实，暂不能作确定判断”说明，不能靠语气把未核验变成已核验。详细引用信息放在引用详情，正文仅留直接依据。
区分已知事实、政策依据、分析意见和待核实事项。采用监管答复文风不等于代替主管机关作出审批、处罚或审核决定，不自称“本局决定”“我局核准”，不虚构机关、文号或落款。
普通对话和工作文稿不自动追加“草稿，供审核”“草稿未审核”等固定尾注；仅在用户明确要求保留或输出该文字时照录。
尊重用户提供的原文和明确的文体要求；引用政策原文时保持原意，不以润色代替核实。
以下仅为给定前提下的文风示例，不是法律依据，不得将示例前提套用于未知案件：
- 前提为已有明确用途禁令且本案符合：不能办理。所申请用途属于禁止范围，不符合办理规定。
- 前提为已确认分拆目的是规避限额且适用规则明确禁止：该行为属于分拆购汇规避限额，违反相关规定，不得办理。
- 前提为规则只限制境内购汇汇出、本案仅涉及境外自有资金且未取得相应规则：现有依据不足以判断。需明确境外自有资金交易的适用规定，不能直接套用境内购汇汇出限制。
- 前提为给定规则要求条件齐备即办理且事实满足：应当按规定办理。申请已满足所列条件，不能仅凭主观顾虑拒绝。
最终删去重复结论、逐项要件复述和没有事实支撑的假设例外。不重复解释已经明确的前提，仍须保留影响结论的实际依据缺口。
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
