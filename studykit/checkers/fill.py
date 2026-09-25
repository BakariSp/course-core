import re

from studykit.checkers import Checker, Verdict, register

BLANK = "____"


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _blanks(key: dict) -> list[list]:
    if "blanks" in key:
        return [b if isinstance(b, list) else [b] for b in key["blanks"]]
    return [key.get("accept", [])]


@register
class Fill(Checker):
    """填空，一题可以有多个空（题干里每个 ____ 是一个空）。response 是每个空的答案列表。"""
    kind = "fill"

    def view(self, q, ctx):
        return {"blanks": max(1, q["prompt"].count(BLANK))}

    def check(self, q, key, ctx, response):
        answers = response if isinstance(response, list) else [response]
        blanks = _blanks(key)
        feedback, right = [], 0
        for i, accept in enumerate(blanks):
            got = answers[i] if i < len(answers) else ""
            if key.get("regex"):
                ok = any(re.fullmatch(p, str(got).strip(), re.IGNORECASE) for p in accept)
            else:
                ok = _norm(got) in {_norm(a) for a in accept}
            right += ok
            feedback.append(f"第 {i + 1} 空：{'✓' if ok else '✗'} 你填的是「{got}」")
        return Verdict(right / len(blanks), feedback)
