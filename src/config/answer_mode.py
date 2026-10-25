"""Request-scoped answer policy; never mutate a process-wide mode."""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from src.prompts.prompts import CASE_ANALYSIS_PROMPT

AnswerMode = Literal['rough', 'interactive', 'precise']
ANSWER_MODES = ('rough', 'interactive', 'precise')
MODE_LABELS = {'rough': '即时答复', 'interactive': '互动研判', 'precise': '审慎研判'}
_mode: ContextVar[AnswerMode] = ContextVar('answer_mode', default='precise')


def current_mode() -> AnswerMode:
    return _mode.get()


@contextmanager
def answer_mode(mode: AnswerMode):
    if mode not in ANSWER_MODES:
        raise ValueError('Unknown answer mode')
    token = _mode.set(mode)
    try:
        yield
    finally:
        _mode.reset(token)


_LIGHTWEIGHT_PROMPT = '''
本模式不逐条核验政策来源、原文完整性或现行效力，不读取官方网页全文，不调用核验专家。
优先使用已提供的事实、附件和可说明局限的已有知识；纯计算、改写、摘要和情景澄清不联网。
涉及最新动态或确实不确定的信息时，可用 WebSearchTool 至多搜索一次线索，不重试、不追加核验。
已有知识及搜索摘要是未经核验的参考；具体政策引文必须来自实际取得的文本，并附真实来源。
未取得效力证据时标为 pending（效力待核验），不能凭官方域名、发布日期或未搜到废止信息宣称有效。
需要核实依据时说明可由用户选择审慎研判，本轮不自动切换模式。
长附件可用 AttachmentReadTool 定向读取，并引用真实文件定位；需要保存报告时用 WriteResult。
工具预算总计至多三次，达到上限后根据已知信息形成条件分析，列明未核实事项，不补造内容。
'''

ROUGH_PROMPT = CASE_ANALYSIS_PROMPT + _LIGHTWEIGHT_PROMPT + '''
当前模式为“即时答复”：直接依据现有信息回答，不追问、不询问处理倾向、不让用户先补充再作答。
本模式覆盖一般交互规则。信息不足时说明缺口及不同条件下的可能结果；无法形成判断时如实说明。
可以列出有助于后续复核的事项，但不得使用问句、选项或要求确认来阻断答复。
结构化 questions 始终为 []。通常约300至600字，遵守用户对篇幅和稿件的明确要求。
'''

INTERACTIVE_PROMPT = CASE_ANALYSIS_PROMPT + _LIGHTWEIGHT_PROMPT + '''
当前模式为“互动研判”：先理解情景和工作人员的纠结点，必要时提出最多两项问题，提供选项并允许自由输入。
处理倾向不清且会影响分析目标时，直接询问其倾向；已经回答或暂不能提供的信息不反复追问。
情景明确后，围绕拟处理意见和相反可能开展条件分析；不逐条核对来源或政策效力。
用户选择“按现有信息分析”或本轮 action 为 analyze_existing 时，questions 必须为 []，立即分析现有信息。
不要要求所有字段齐全，不要重复询问是否需要开始分析，不以追问代替可完成的分析。
'''
