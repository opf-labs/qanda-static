#!/usr/bin/env python3
"""Render a static archive of the OPF Question2Answer site from its database.

Reads directly from a MySQL/MariaDB instance holding the qanda dump and writes
plain HTML into the output directory. URLs mirror Question2Answer's own scheme
(`/<id>/`, `/user/<handle>/`, `/tags/<tag>`) so old inbound links keep working
against the static copy; see nginx.conf for the legacy rewrites.

Connection is configured via environment variables:

    QA_DB_HOST (default 127.0.0.1)   QA_DB_PORT (default 3307)
    QA_DB_USER (default root)        QA_DB_PASSWORD (default root)
    QA_DB_NAME (default qanda)       QA_TABLE_PREFIX (default qa_)
    QA_OUT_DIR (default ../site)     QA_ARCHIVED (default: current month)
"""
import html
import json
import os
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import pymysql
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from qa_common import (ASSETS, AVATARS, MEDIA, PREFIX, connect_with_retry,
                       is_removed, load_removed)

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"

# original content-image URL -> local file under assets/media (see extract_media.py)
MEDIA_MAP = {}

OUT = Path(os.environ.get("QA_OUT_DIR", ASSETS.parent / "site")).resolve()
ARCHIVED = os.environ.get("QA_ARCHIVED", date.today().strftime("%B %Y"))

MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# --- content rendering -------------------------------------------------------

_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_DANGEROUS_TAGS = re.compile(
    r"<\s*/?\s*(script|style|iframe|object|embed|form|link|meta|base|applet)\b[^>]*>", re.I)
_ON_ATTR = re.compile(r"""\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.I)
_JS_URI = re.compile(r"""(href|src)\s*=\s*("|')\s*javascript:[^"']*\2""", re.I)
_JS_URI_BARE = re.compile(r"""(href|src)\s*=\s*javascript:[^\s>]*""", re.I)
_URL = re.compile(r"""(https?://[^\s<>"']+)""")


def clean_html(raw: str) -> str:
    """Best-effort sanitiser for trusted-but-old rich-text HTML. The corpus is
    real contributors' posts (spam lived in accounts, not content) and currently
    contains no active markup, but the output is emitted as unescaped Markup, so
    strip active elements and javascript: URIs defensively."""
    out = _SCRIPT_STYLE.sub("", raw)
    out = _DANGEROUS_TAGS.sub("", out)
    out = _ON_ATTR.sub("", out)
    out = _JS_URI.sub(r"\1=\2#\2", out)
    out = _JS_URI_BARE.sub(r"\1=#", out)
    return out


def text_to_html(raw: str) -> str:
    """Plain-text post: escape, autolink URLs, keep line breaks."""
    esc = html.escape(raw)
    esc = _URL.sub(r'<a href="\1" rel="nofollow">\1</a>', esc)
    return esc.replace("\n", "<br>\n")


def render_body(content: str, fmt: str) -> Markup:
    content = content or ""
    body = clean_html(content) if fmt == "html" else text_to_html(content)
    # Point embedded images at the mirrored local copies. Post bodies only ever
    # render on question pages, which all sit one level deep, so ../ is correct.
    if MEDIA_MAP and "<img" in body:
        for url, name in MEDIA_MAP.items():
            body = body.replace(f'src="{url}"', f'src="../assets/media/{name}"')
            body = body.replace(f"src='{url}'", f"src='../assets/media/{name}'")
    return Markup(body)


def fmt_date(dt) -> str:
    if not isinstance(dt, datetime):
        return ""
    return f"{dt.day} {MONTHS[dt.month]} {dt.year}"


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    s = _SLUG_STRIP.sub("-", (value or "").lower()).strip("-")
    return s[:60].rstrip("-") or "item"


def allocate(base, taken, reserved=frozenset()):
    """Return a name unique within `taken` (and not in `reserved`), suffixing
    -2, -3, ... on collision. Guards against two tags or handles that reduce to
    the same slug/directory silently overwriting one another."""
    name = base
    n = 2
    while name in taken or name in reserved:
        name = f"{base}-{n}"
        n += 1
    taken.add(name)
    return name


def user_dir(handle: str) -> str:
    """Filesystem-safe directory name for a user, kept as close to the raw
    handle as possible so /user/<handle> resolves against the static copy."""
    safe = (handle or "").replace("/", "-").replace("\\", "-")
    safe = "".join(ch for ch in safe if ch >= " ").strip()
    return safe or "user"


# --- data access -------------------------------------------------------------

def scan_avatars():
    """Map userid -> relative avatar path for the uploaded avatars extract_media.py
    captured from the blob store."""
    found = {}
    if AVATARS.exists():
        for f in AVATARS.iterdir():
            m = re.match(r"u(\d+)\.", f.name)
            if m:
                found[int(m.group(1))] = f"assets/avatars/{f.name}"
    return found


def resolve_avatar(userid, user, avatar_files, removed):
    """Every published avatar is a local file captured by extract_media.py, so no
    page discloses a contributor's email hash to a third party at view time.
    Contributors on the removal list are never rendered."""
    if is_removed(removed, userid, user["handle"]):
        return None
    return avatar_files.get(userid)


def load(conn):
    cur = conn.cursor(pymysql.cursors.DictCursor)
    avatar_files = scan_avatars()
    removed = load_removed()

    cur.execute(
        f"""SELECT u.userid, u.handle, u.created,
                   COALESCE(p.points,0) points, COALESCE(p.qposts,0) qposts,
                   COALESCE(p.aposts,0) aposts, COALESCE(p.cposts,0) cposts
            FROM {PREFIX}users u
            LEFT JOIN {PREFIX}userpoints p ON p.userid = u.userid""")
    users_by_id = {r["userid"]: r for r in cur.fetchall()}

    authors = {}
    taken_dirs = set()

    def get_author(userid):
        if userid in authors:
            return authors[userid]
        u = users_by_id.get(userid)
        if not u:
            return None
        directory = allocate(user_dir(u["handle"]), taken_dirs)
        prof = dict(handle=u["handle"], dir=directory,
                    href=f"user/{quote(directory, safe='')}/",
                    joined=fmt_date(u["created"]),
                    avatar=resolve_avatar(userid, u, avatar_files, removed),
                    points=u["points"], qposts=u["qposts"], aposts=u["aposts"],
                    cposts=u["cposts"], questions=[], answers=[], comments=0)
        authors[userid] = prof
        return prof

    def attribute(row):
        prof = get_author(row["userid"])
        row["author"] = prof["handle"] if prof else (row.get("name") or "anonymous")
        row["author_href"] = prof["href"] if prof else None
        row["author_avatar"] = prof["avatar"] if prof else None
        return prof

    cols = ("postid, parentid, userid, name, type, format, title, content, tags, "
            "netvotes, views, selchildid, acount, created")
    cur.execute(f"SELECT {cols} FROM {PREFIX}posts WHERE type IN ('Q','A','C')")
    rows = cur.fetchall()

    questions, answers, comments = {}, [], []
    for r in rows:
        r["created_date"] = fmt_date(r["created"])
        r["body"] = render_body(r["content"], r["format"])
        if r["type"] == "Q":
            r["tags"] = [t.strip() for t in (r["tags"] or "").split(",") if t.strip()]
            r["url"] = f"{r['postid']}/"
            r["path"] = f"{r['postid']}/index.html"
            r["answers"] = []
            r["comments"] = []
            questions[r["postid"]] = r
        elif r["type"] == "A":
            r["anchor"] = f"a{r['postid']}"
            answers.append(r)
        else:
            comments.append(r)

    comments_by_parent = {}
    for c in sorted(comments, key=lambda x: x["created"] or datetime.min):
        prof = attribute(c)
        if prof:
            prof["comments"] += 1
        comments_by_parent.setdefault(c["parentid"], []).append(c)

    answers_by_q = {}
    for a in answers:
        a["comments"] = comments_by_parent.get(a["postid"], [])
        answers_by_q.setdefault(a["parentid"], []).append(a)

    for qid, q in questions.items():
        prof = attribute(q)
        if prof:
            prof["questions"].append(q)
        q["comments"] = comments_by_parent.get(qid, [])
        ans = answers_by_q.get(qid, [])
        accepted_id = q["selchildid"] if any(a["postid"] == q["selchildid"] for a in ans) else None
        q["has_accepted"] = accepted_id is not None
        q["answers_anchor"] = f"#a{accepted_id}" if accepted_id else "#answers"
        for a in ans:
            a["accepted"] = a["postid"] == accepted_id
            a["question"] = q
            prof = attribute(a)
            if prof:
                prof["answers"].append(a)
        ans.sort(key=lambda a: (not a["accepted"], -(a["netvotes"] or 0),
                                a["created"] or datetime.min))
        q["answers"] = ans

    return questions, authors


# --- site build --------------------------------------------------------------

def clear_dir(path: Path):
    """Empty a directory without removing it (it may be a bind-mount root)."""
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def build():
    global MEDIA_MAP
    manifest = MEDIA / "manifest.json"
    if manifest.exists():
        MEDIA_MAP = json.loads(manifest.read_text())

    conn = connect_with_retry()
    try:
        questions, authors = load(conn)
    finally:
        conn.close()

    q_sorted = sorted(questions.values(),
                      key=lambda q: q["created"] or datetime.min, reverse=True)

    tag_slugs, tag_counts, tag_questions = {}, {}, {}
    taken_tag_slugs = set()
    for q in q_sorted:
        for t in q["tags"]:
            if t not in tag_slugs:
                tag_slugs[t] = allocate(slugify(t), taken_tag_slugs, reserved={"index"})
            tag_counts[t] = tag_counts.get(t, 0) + 1
            tag_questions.setdefault(t, []).append(q)

    n_answers = sum(len(q["answers"]) for q in questions.values())
    n_comments = sum(len(q["comments"]) + sum(len(a["comments"]) for a in q["answers"])
                     for q in questions.values())
    years = [q["created"].year for q in questions.values() if q["created"]]
    date_range = f"{min(years)} to {max(years)}" if years else "unknown"

    site = dict(
        name="OPF Q&A", questions=len(questions), answers=n_answers,
        comments=n_comments, tags=len(tag_counts), users=len(authors),
        archived=ARCHIVED, date_range=date_range,
    )

    env = Environment(loader=FileSystemLoader(str(TEMPLATES)),
                      autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["slug"] = slugify

    clear_dir(OUT)

    def write(path, template, **ctx):
        target = OUT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        root = "../" * path.count("/")
        target.write_text(env.get_template(template).render(site=site, root=root, **ctx),
                          encoding="utf-8")

    write("index.html", "index.html", questions=q_sorted)
    write("about.html", "about.html")

    tags_sorted = sorted(
        ({"name": t, "slug": tag_slugs[t], "count": tag_counts[t]} for t in tag_counts),
        key=lambda t: (-t["count"], t["name"]))
    write("tags/index.html", "tags.html", tags=tags_sorted)
    for t, qs in tag_questions.items():
        write(f"tags/{tag_slugs[t]}.html", "tag.html", tag=t, questions=qs)

    for q in q_sorted:
        write(q["path"], "question.html", q=q)

    for prof in authors.values():
        prof["questions"].sort(key=lambda q: q["created"] or datetime.min, reverse=True)
        prof["answers"].sort(key=lambda a: a["created"] or datetime.min, reverse=True)
        write(f"user/{prof['dir']}/index.html", "user.html", profile=prof)

    # removed.txt names people who asked to be taken out; it is build input, not
    # something to republish. manifest.json is likewise only useful to the build.
    shutil.copytree(ASSETS, OUT / "assets",
                    ignore=shutil.ignore_patterns("removed.txt", "manifest.json"))

    print(f"Built {len(questions)} questions, {n_answers} answers, {n_comments} comments, "
          f"{len(tag_counts)} tags, {len(authors)} user profiles -> {OUT}")


if __name__ == "__main__":
    build()
