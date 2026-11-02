#!/usr/bin/env python3
"""Check a slide outline (see references/outline-template.md) and build the Canva prompt.

    outline_check.py OUTLINE.md              findings, exit 1 if any error
    outline_check.py --json OUTLINE.md       findings as JSON
    outline_check.py --prompt OUTLINE.md     prompt for Canva generate-design (refused on errors)

Sensitive data is reported by kind and line only; matched values are never printed.
Python 3 standard library only.
"""

import argparse
import json
import re
import sys

REQUIRED_META = ("title", "audience", "goal", "duration_min")
MAX_BULLETS = 5
MAX_BULLET_CHARS = 90
MAX_TITLE_CHARS = 70

SLIDE_RE = re.compile(r"^##\s+(\d+)\.\s*(.*?)\s*$")
BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*\S)\s*$")
VISUAL_RE = re.compile(r"^visual\s*:\s*(.*\S)\s*$", re.IGNORECASE)
NOTE_RE = re.compile(r"^>\s?(.*)$")
PLACEHOLDER_RE = re.compile(r"\[(?:cần số liệu|can so lieu|need data|todo)\s*:", re.IGNORECASE)
CLOSING_RE = re.compile(r"^(cảm ơn|cám ơn|thank you|thanks|q\s*&\s*a|hỏi đáp)[\s.!]*$", re.IGNORECASE)

SENSITIVE = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("token", re.compile(
        r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|xox[abprs]-[A-Za-z0-9-]{10,}"
        r"|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}", re.IGNORECASE)),
    ("credential", re.compile(
        r"\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|token|mật khẩu)\s*[:=]\s*\S{4,}",
        re.IGNORECASE)),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<![\d.,])(?:\+84|0084|0)(?:3|5|7|8|9)\d{8}(?![\d.,])")),
    ("private-ip", re.compile(
        r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b")),
    ("internal-host", re.compile(r"\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:internal|local|corp|lan|intranet)\b",
                                 re.IGNORECASE)),
]
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
NATIONAL_ID_RE = re.compile(r"(?<![\d.,])\d{12}(?![\d.,])")
URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)


def luhn(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


class Slide:
    def __init__(self, number, title, line):
        self.number = number
        self.title = title
        self.line = line
        self.bullets = []   # (depth, text, line)
        self.visual = None
        self.notes = []

    def as_dict(self):
        return {
            "number": self.number,
            "title": self.title,
            "line": self.line,
            "bullets": [{"depth": d, "text": t} for d, t, _ in self.bullets],
            "visual": self.visual,
            "notes": " ".join(self.notes).strip() or None,
        }


def parse(text):
    lines = text.splitlines()
    meta, start = {}, 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
            key, sep, value = lines[i].partition(":")
            if sep:
                meta[key.strip().lower()] = value.strip().strip("\"'")
        else:
            start = 0
            meta = {}
    slides, current, in_fence = [], None, False
    for index in range(start, len(lines)):
        raw = lines[index]
        number = index + 1
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = SLIDE_RE.match(raw)
        if m:
            current = Slide(int(m.group(1)), m.group(2), number)
            slides.append(current)
            continue
        if current is None:
            continue
        stripped = raw.strip()
        m = NOTE_RE.match(stripped)
        if m:
            current.notes.append(m.group(1))
            continue
        m = VISUAL_RE.match(stripped)
        if m:
            current.visual = m.group(1)
            continue
        m = BULLET_RE.match(raw)
        if m:
            depth = 1 if len(m.group(1).replace("\t", "  ")) >= 2 else 0
            current.bullets.append((depth, m.group(2), number))
    return meta, slides, lines


def sensitive_findings(lines):
    out = []
    for number, line in enumerate(lines, 1):
        kinds = [kind for kind, rx in SENSITIVE if rx.search(line)]
        for m in CARD_RE.finditer(line):
            digits = re.sub(r"\D", "", m.group(0))
            if 13 <= len(digits) <= 19 and luhn(digits):
                kinds.append("card-number")
                break
        if NATIONAL_ID_RE.search(line):
            kinds.append("national-id")
        for kind in dict.fromkeys(kinds):
            out.append(("error", "sensitive", number, f"{kind}: remove or anonymise before sending to Canva"))
        if URL_RE.search(line) and not kinds:
            out.append(("warn", "url", number, "URL on a slide: make sure it is public and needed"))
    return out


def check(meta, slides, lines):
    findings = []  # (level, kind, line, message)
    for key in REQUIRED_META:
        if not meta.get(key):
            findings.append(("error", "meta", 1, f"front matter is missing '{key}'"))
    duration = None
    if meta.get("duration_min"):
        try:
            duration = float(meta["duration_min"])
            if duration <= 0:
                raise ValueError
        except ValueError:
            findings.append(("error", "meta", 1, "duration_min must be a positive number"))
            duration = None
    if not slides:
        findings.append(("error", "structure", 1, "no slides: use '## 1. Title' headings"))
    for expected, slide in enumerate(slides, 1):
        if slide.number != expected:
            findings.append(("error", "structure", slide.line, f"slide numbered {slide.number}, expected {expected}"))
        if not slide.title:
            findings.append(("error", "structure", slide.line, "slide has no title"))
        elif len(slide.title) > MAX_TITLE_CHARS:
            findings.append(("warn", "title", slide.line,
                             f"title is {len(slide.title)} characters (max {MAX_TITLE_CHARS})"))
        if len(slide.bullets) > MAX_BULLETS:
            findings.append(("warn", "bullets", slide.line,
                             f"{len(slide.bullets)} bullets (max {MAX_BULLETS}): split the slide or move detail to notes"))
        for _, text, number in slide.bullets:
            if len(text) > MAX_BULLET_CHARS:
                findings.append(("warn", "bullet-length", number,
                                 f"bullet is {len(text)} characters (max {MAX_BULLET_CHARS})"))
        if expected > 1 and not slide.bullets and not slide.visual:
            findings.append(("warn", "empty", slide.line, "slide has neither bullets nor a Visual line"))
    if slides and CLOSING_RE.match(slides[-1].title or "") and not slides[-1].bullets:
        findings.append(("warn", "closing", slides[-1].line,
                         "last slide is only a closing word: end with the decision or next steps"))
    if duration and slides:
        count = len(slides)
        if count > duration * 1.5 + 2:
            findings.append(("warn", "pace", 1, f"{count} slides for {duration:g} min is too many"))
        elif count < max(2, duration / 3):
            findings.append(("warn", "pace", 1, f"{count} slides for {duration:g} min is too few"))
    for number, line in enumerate(lines, 1):
        if PLACEHOLDER_RE.search(line):
            findings.append(("warn", "missing-data", number, "placeholder for missing data"))
    findings.extend(sensitive_findings(lines))
    without_notes = [s.number for s in slides if not s.notes]
    if slides and without_notes:
        findings.append(("info", "notes", 1, f"{len(without_notes)} slide(s) without speaker notes"))
    findings.sort(key=lambda f: ({"error": 0, "warn": 1, "info": 2}[f[0]], f[2]))
    return findings


LANGUAGES = {"vi": "Vietnamese (with full diacritics)", "en": "English"}


def build_prompt(meta, slides, max_chars=0):
    language = meta.get("language", "").lower()
    language = LANGUAGES.get(language, meta.get("language") or "the language of the outline")

    def render(level):
        out = [
            f"Presentation: {meta.get('title', '')}",
            f"Audience: {meta.get('audience', '')}",
            f"Goal: {meta.get('goal', '')}",
            f"Talk length: {meta.get('duration_min', '')} minutes",
            f"Language: {language}. Write every slide in this language; keep technical terms, "
            "code names and issue keys exactly as written.",
            "Style: clean, professional, one message per slide, short bullets, no stock filler text.",
            f"Slides ({len(slides)}), keep this order and these titles:",
        ]
        for s in slides:
            out.append(f"{s.number}. {s.title}")
            bullets = s.bullets
            if level >= 1:
                bullets = [b for b in bullets if b[0] == 0]
            if level >= 3:
                bullets = bullets[:2]
            if level >= 4:
                bullets = []
            for depth, text, _ in bullets:
                out.append(("    - " if depth else "  - ") + text)
            if s.visual and level < 2:
                out.append(f"  Visual: {s.visual}")
        return "\n".join(out)

    for level in range(5):
        text = render(level)
        if not max_chars or len(text) <= max_chars:
            return text, level
    return text, 4


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("outline")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="findings and parsed slides as JSON")
    mode.add_argument("--prompt", action="store_true", help="print the Canva generate-design prompt")
    parser.add_argument("--max-chars", type=int, default=0,
                        help="with --prompt: shorten (sub-bullets, visuals, bullets) to fit")
    args = parser.parse_args(argv)
    try:
        with open(args.outline, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read {args.outline}: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    meta, slides, lines = parse(text)
    findings = check(meta, slides, lines)
    errors = [f for f in findings if f[0] == "error"]
    placeholders = [f for f in findings if f[1] == "missing-data"]

    if args.prompt:
        if errors or placeholders:
            print(f"refused: {len(errors)} error(s), {len(placeholders)} missing-data placeholder(s); "
                  "run without --prompt to see them", file=sys.stderr)
            return 1
        prompt, level = build_prompt(meta, slides, args.max_chars)
        if level and len(prompt) > args.max_chars:
            print(f"note: prompt is {len(prompt)} characters even with titles only "
                  f"(limit {args.max_chars}): split the deck", file=sys.stderr)
        elif level:
            print(f"note: prompt shortened (level {level}) to fit {args.max_chars} characters", file=sys.stderr)
        print(prompt)
        return 0

    if args.json:
        print(json.dumps({
            "meta": meta,
            "slides": [s.as_dict() for s in slides],
            "findings": [{"level": l, "kind": k, "line": n, "message": m} for l, k, n, m in findings],
            "errors": len(errors),
        }, ensure_ascii=False, indent=2))
    else:
        for level, kind, number, message in findings:
            print(f"{args.outline}:{number}: {level} {kind}: {message}")
        warns = sum(1 for f in findings if f[0] == "warn")
        print(f"{len(slides)} slide(s), {len(errors)} error(s), {warns} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
