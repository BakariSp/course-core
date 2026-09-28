"""学习者资料（D-047）：学习者自己写的那部分（progress/profile.yaml）。

INVARIANT: 这里只放学习者自己说的事（我是谁、我要什么、我有多少时间）。
老师对学习者的观察（什么讲法有效、在哪会过载）不在这里：它们是证据（proposed_strategy / refuted_strategy），
由 observations() 投影出"现在还有效的观察"，每条带出处，学习者能说不对。
"""
from __future__ import annotations

from dataclasses import dataclass

from studykit.domain.errors import DomainError
from studykit.domain.evidence import Evidence

SCHEMA_VERSION = 1

# 资料字段：key → 界面上的名字。顺序就是界面和简报里的顺序。
ABOUT_FIELDS = {
    "identity": "身份",
    "background": "编程基础",
    "starting_point": "起点",
    "goal": "学习目标",
    "style": "学习方式",
    "machine": "电脑",
    "language": "语言",
}
TIME_FIELDS = {"session_minutes": ("每次坐下来学多久", 45), "unit_budget_minutes": ("一个单元最多学多久", 180),
               "max_new_terms": ("每节最多几个新词", 5)}


class ProfileError(DomainError):
    pass


@dataclass(frozen=True)
class Profile:
    about: dict[str, str]
    time: dict[str, int]

    def render(self) -> str:
        """给 agent 的学习者资料（只含学习者自己写的）。"""
        return "\n".join(f"- {ABOUT_FIELDS[k]}：{v}" for k, v in self.about.items() if v)


def parse_profile(data: dict) -> Profile:
    data = data or {}
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ProfileError(f"profile.yaml 的 schema_version 要是 {SCHEMA_VERSION}")
    about = data.get("about") or {}
    unknown = set(about) - set(ABOUT_FIELDS)
    if unknown:
        raise ProfileError(f"profile.yaml 里不认识的字段：{', '.join(sorted(unknown))}（可用：{', '.join(ABOUT_FIELDS)}）")
    time = data.get("time") or {}
    return Profile({k: str(about[k]).strip() for k in ABOUT_FIELDS if about.get(k)},
                   {k: int(time.get(k) or default) for k, (_, default) in TIME_FIELDS.items()})


# ---------- 老师的观察：证据的投影 ----------

def observations(evidence: list[Evidence]) -> list[dict]:
    """现在还有效的观察：proposed_strategy 里没被 refuted_strategy 推翻的，按时间顺序。每条带出处和依据。"""
    refuted = {e.caused_by: e for e in evidence if e.verb == "refuted_strategy"}
    out = []
    for e in evidence:
        if not (e.verb == "proposed_strategy" and e.id not in refuted):
            continue
        out.append({"id": e.id, "text": e.payload["text"], "scope": e.payload.get("scope", "teaching"),
                    "source": e.payload.get("source", ""), "by": f"{e.actor.type}:{e.actor.id}", "ts": e.ts})
    return out


def render_observations(obs: list[dict]) -> str:
    return "\n".join(f"- {o['text']}（{o['source'] or o['ts'][:10]}）" for o in obs) or "（还没有）"
