"""视频页面 → 字幕（D-043）。实现 app.ports.Fetcher：YouTube 的视频页返回带时间标记的字幕，播放列表返回视频清单；
其他网址交给内层的网页抓取。字幕用 yt-dlp 取（只取字幕，不下载视频），按视频 id 缓存，和学习者无关。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from studykit.app.ports import Fetcher, FetchError, Page
from studykit.domain.transcript import clock, vtt_to_text, youtube_target


def _run(args: list[str], cwd: Path | None = None, timeout: int = 120) -> str:
    proc = subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-300:] or f"yt-dlp 退出码 {proc.returncode}")
    return proc.stdout


class VideoFetcher:
    def __init__(self, inner: Fetcher, cache: Path, run=_run):
        self.inner, self.cache, self.run = inner, cache, run

    def fetch(self, url: str, timeout: float = 20.0) -> Page:
        target = youtube_target(url)
        if target is None:
            return self.inner.fetch(url, timeout)
        kind, vid = target
        try:
            return self._video(vid) if kind == "video" else self._playlist(vid)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            raise FetchError(url, None, f"取字幕失败：{e}") from e

    def _video(self, vid: str) -> Page:
        url = f"https://www.youtube.com/watch?v={vid}"
        hit = self.cache / f"{vid}.json"
        if hit.exists():
            d = json.loads(hit.read_text(encoding="utf-8"))
        else:
            with tempfile.TemporaryDirectory() as tmp:
                # 人工字幕优先，没有再用自动字幕；只要英文
                # WHY: --print 默认只模拟、不写文件，要加 --no-simulate 字幕才会落盘
                meta = self.run(["--skip-download", "--no-simulate", "--write-subs", "--write-auto-subs", "--sub-langs", "en,en-.*",
                                 "--sub-format", "vtt", "--print", "%(title)s\t%(duration)s", "-o", "%(id)s", url], Path(tmp))
                subs = sorted(Path(tmp).glob(f"{vid}*.vtt"), key=lambda p: (p.name.count("-orig"), len(p.name)))
                if not subs:
                    raise RuntimeError("这个视频没有英文字幕")
                title, _, dur = meta.strip().splitlines()[-1].partition("\t")
                d = {"title": title, "duration": int(float(dur or 0)),
                     "text": vtt_to_text(subs[0].read_text(encoding="utf-8", errors="replace"))}
            self.cache.mkdir(parents=True, exist_ok=True)
            hit.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        head = (f"（视频字幕，时长 {clock(d['duration'])}。每段开头的 [mm:ss] 是视频里的时间；"
                f"引用某一段时写时间段，如 12:30-18:40）\n\n")
        return Page(200, url, f"{d['title']}（字幕）", head + d["text"], [])

    def _playlist(self, pid: str) -> Page:
        url = f"https://www.youtube.com/playlist?list={pid}"
        out = self.run(["--flat-playlist", "--print", "%(id)s\t%(title)s", url])
        rows = [l.split("\t", 1) for l in out.splitlines() if "\t" in l]
        links = [(f"https://www.youtube.com/watch?v={v}", t) for v, t in rows]
        text = "播放列表里的视频（打开视频网址读到的是带时间标记的字幕）：\n" + "\n".join(f"- {t} → {u}" for u, t in links)
        return Page(200, url, "YouTube 播放列表", text, links)
