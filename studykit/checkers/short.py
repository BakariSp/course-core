from studykit.checkers import Checker, Verdict, register


@register
class Short(Checker):
    """简答题（解释、找 bug、项目映射）。只记录回答，由导师按 key.yaml 里的 rubric 批改。"""
    kind = "short"
    auto = False

    def check(self, q, key, ctx, response):
        return Verdict(None, ["已提交，等导师批改。"])
