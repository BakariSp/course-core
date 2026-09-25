from studykit.checkers import Checker, Verdict, register


def _letters(value) -> set[str]:
    items = value if isinstance(value, list) else [value]
    return {str(v).strip().upper() for v in items if str(v).strip()}


@register
class Choice(Checker):
    """单选 / 多选。response 是选中的字母列表，如 ["A", "C"]。"""
    kind = "choice"

    def view(self, q, ctx):
        return {"options": q["options"], "multi": bool(q.get("multi"))}

    def check(self, q, key, ctx, response):
        want, got = _letters(key["answer"]), _letters(response or [])
        if not q.get("multi"):
            score = 1.0 if got == want else 0.0
        else:
            # WHY: 多选按"选对的减去选错的"给部分分，防止全选也能拿分。
            score = max(0.0, (len(got & want) - len(got - want)) / len(want))
        return Verdict(score, [f"正确答案：{'、'.join(sorted(want))}"])
