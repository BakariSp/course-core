"""视频字幕（D-043）：解析、按时间段切片、YouTube 抓取适配器（不联网，yt-dlp 用假的）。"""
from pathlib import Path

import pytest

from studykit.adapters.video_fetch import VideoFetcher
from studykit.app.ports import FetchError, Page
from studykit.domain.transcript import duration, parse_range, slice_text, vtt_to_text, youtube_target

VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.968 --> 00:00:01.328
All right,

00:00:01.348 --> 00:00:02.849
<c>let's</c> get started.

00:00:02.900 --> 00:00:03.000
let's get started.

00:00:40.000 --> 00:00:42.000
Logging is printing with care.

01:02:03.000 --> 01:02:05.000
The end.
"""


def test_vtt_becomes_timestamped_paragraphs_without_tags_or_rolling_repeats():
    text = vtt_to_text(VTT)
    assert text.splitlines() == ["[00:00] All right, let's get started.",
                                 "[00:40] Logging is printing with care.",
                                 "[1:02:03] The end."]
    assert duration(text) == 3723


def test_ranges_and_slices():
    assert parse_range("12:30-18:40") == (750, 1120)
    assert parse_range("1:02:03 – 1:05:00") == (3723, 3900)
    assert parse_range("18:40-12:30") is None and parse_range("第三段") is None and parse_range("12:75-13:00") is None
    text = "[00:00] a\n[01:00] b\n[02:00] c\n[05:00] d"
    assert slice_text(text, 60, 120, pad=0) == "[01:00] b\n[02:00] c"
    assert slice_text(text, 90, 100) == "[01:00] b\n[02:00] c"     # 默认前后各留 30 秒
    assert slice_text(text, 200, 250) == ""


@pytest.mark.parametrize("url,want", [
    ("https://www.youtube.com/watch?v=8VYT9TcUmKs", ("video", "8VYT9TcUmKs")),
    ("https://youtu.be/8VYT9TcUmKs", ("video", "8VYT9TcUmKs")),
    ("https://www.youtube.com/playlist?list=PLyzOVJj3bHQ", ("playlist", "PLyzOVJj3bHQ")),
    ("https://www.youtube.com/watch?v=x;rm", None),
    ("https://missing.csail.mit.edu/2026/", None),
])
def test_youtube_target(url, want):
    assert youtube_target(url) == want


class Inner:
    def fetch(self, url, timeout=20.0):
        return Page(200, url, "网页", "正文", [])


def fake_ytdlp(calls):
    def run(args, cwd=None, timeout=120):
        calls.append(args)
        if "--flat-playlist" in args:
            return "abcdef1\tLecture 1\nabcdef2\tLecture 2\n"
        (cwd / "abcdef1.en.vtt").write_text(VTT, encoding="utf-8")
        return "Lecture 1\t3725\n"
    return run


def test_video_page_is_the_transcript_and_is_cached(tmp_path: Path):
    calls = []
    f = VideoFetcher(Inner(), tmp_path / "cache", run=fake_ytdlp(calls))
    page = f.fetch("https://youtu.be/abcdef1")
    assert page.final_url == "https://www.youtube.com/watch?v=abcdef1"
    assert "Lecture 1" in page.title and "1:02:05" in page.text and "[00:40] Logging is printing with care." in page.text
    assert f.fetch("https://www.youtube.com/watch?v=abcdef1").text == page.text
    assert len(calls) == 1                      # 第二次读缓存
    assert f.fetch("https://missing.csail.mit.edu/x").title == "网页"


def test_playlist_lists_videos_as_links(tmp_path: Path):
    f = VideoFetcher(Inner(), tmp_path, run=fake_ytdlp([]))
    page = f.fetch("https://www.youtube.com/playlist?list=PLx")
    assert page.links == [("https://www.youtube.com/watch?v=abcdef1", "Lecture 1"),
                          ("https://www.youtube.com/watch?v=abcdef2", "Lecture 2")]


def test_no_subtitles_is_a_fetch_error(tmp_path: Path):
    f = VideoFetcher(Inner(), tmp_path, run=lambda args, cwd=None, timeout=120: "T\t10\n")
    with pytest.raises(FetchError, match="没有英文字幕"):
        f.fetch("https://youtu.be/abcdef1")


# ---------- 写一节时：统一的"学习者此刻会什么"、只给这一节那段字幕（D-043） ----------

from studykit.app.harness import Harness  # noqa: E402

OUTLINE = {"parts": [{"title": "p", "sections": [
    {"title": "print 调试", "terms": [{"id": "tools.debug.print", "term": "打印调试"}], "teaches": ["在可疑的地方打印中间值"]},
    {"title": "日志", "terms": [{"id": "tools.debug.logging", "term": "日志"}], "teaches": ["日志是留在代码里的 print"],
     "video": "01:00-02:00", "reading": ["https://www.youtube.com/watch?v=abcdef1"]},
    {"title": "调试器", "terms": [], "teaches": ["停在某一行"]}]}]}


def test_learner_now_is_baseline_plus_earlier_sections_only():
    inp = {"known_titles": ["管道"], "known_terms": ["grep", "tools.cmd.grep", ".gitignore"]}
    text = Harness.learner_now(OUTLINE, 2, inp)
    assert "管道" in text and "grep、.gitignore" in text and "tools.cmd.grep" not in text
    assert "第 1 节「print 调试」学过：打印调试" in text and "日志是留在代码里的 print" in text
    assert "调试器" not in text                                     # 这一节和后面的不算
    assert "第 1 节" not in Harness.learner_now(OUTLINE, 0, inp)


def test_section_reading_gets_only_its_slice_of_the_transcript():
    video = {"url": "https://www.youtube.com/watch?v=abcdef1", "ok": True, "start": 0,
             "text": "[00:00] intro\n[01:10] logging is print that stays\n[03:00] debuggers"}
    notes = {"url": "https://missing.csail.mit.edu/x", "ok": True, "start": 0, "text": "讲义全文"}
    got = Harness._reading([video, notes], ["https://www.youtube.com/watch?v=abcdef1", "https://missing.csail.mit.edu/x"], (60, 120))
    assert "logging is print that stays" in got and "intro" not in got and "debuggers" not in got
    assert "讲义全文" in got                                        # 讲义不切
    assert "intro" in Harness._reading([video], ["https://www.youtube.com/watch?v=abcdef1"])   # 大纲没给时间段：照旧


def test_outline_video_range_must_be_a_real_range():
    from studykit.domain.plan_check import PlanLimits, outline_findings
    bad = {"parts": [{"title": "p", "sections": [dict(OUTLINE["parts"][0]["sections"][1], video="第二段")]}]}
    assert any("video 写成" in f.what for f in outline_findings(bad, PlanLimits("tools-03-debug")))
