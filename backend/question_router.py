"""问题分类路由（生产）：按问题类型把回答任务路由到不同成本的模型。

依据 eval 横评（eval/route_speed_eval.py，100 题）：
- 规则分类 5 类准确率 85% / 路由二分准确率 90% / 零延迟，与 haiku 分类等效但不耗 LLM。
- 按类路由后端到端质量 4.45（≈全 opus 4.58、优于全 sonnet 4.29），完整耗时较全 opus -42%，
  opus 调用占比降到 ~31%。
- 关键：负责人/方案/歧义类对模型质量敏感，路由回强模型（opus）守住质量；数量/排查类
  sonnet 质量已够用，走快模型提速。

路由表与分类规则均与 eval/classifier_eval.py 保持一致（同一套规则，离线已验证）。
强/快模型名取自配置：强模型=settings.llm_model，快模型=settings.llm_model_fast。
快模型未配置（空）时，所有问题回退强模型 —— 等价于未启用路由，零回归。
"""
import re

# 需要强模型的类型（质量敏感）；其余走快模型
_STRONG_TYPES = {"负责人类", "方案类", "歧义类"}


def classify_question(q: str) -> str:
    """规则分类（零成本），返回 5 类之一。与 eval/classifier_eval.classify_rule 同逻辑。

    优先级从强信号到弱信号，最后兜底排查类（最大类）。
    """
    if re.search(r'谁|负责|跟进|对接|是谁|找谁|owner|负责人', q, re.I):
        return "负责人类"
    if re.search(r'几个|几台|多少|多久|几次|几条|比例|占比|总共|一共|多大|几种|人数', q):
        return "数量类"
    if re.search(r'到底|还是|区别|矛盾|算不算|哪个是|是.+还是|有点', q):
        return "歧义类"
    if re.search(r'怎么搞|怎么做|怎么弄|如何|能不能|可以.*吗|为什么能|是不是有', q):
        return "方案类"
    return "排查类"


def route_model(question: str, strong_model: str, fast_model: str = "") -> tuple[str, str]:
    """返回 (选定模型, 问题类型)。

    fast_model 为空 → 一律返回 strong_model（等价未启用路由，零回归）。
    质量敏感类型走 strong_model，其余走 fast_model。
    """
    qtype = classify_question(question)
    if not fast_model:
        return strong_model, qtype
    model = strong_model if qtype in _STRONG_TYPES else fast_model
    return model, qtype
