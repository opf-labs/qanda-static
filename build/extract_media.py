#!/usr/bin/env python3
"""Capture the images the archive must own, into the source assets/ tree.

Two sources, written under assets/ so they become part of the committed
preservation copy and are picked up by generate.py:

  assets/avatars/u<userid>.<ext> contributor avatars: uploads from the Q2A blob store,
                                 plus Gravatars fetched once by email hash. Cached so no
                                 page ever discloses a contributor's email hash to a
                                 third party at view time.
  assets/avatars/removed.txt     opt-out list; these contributors are never fetched
  assets/media/<sha1>.<ext>      images embedded in post content, mirrored locally
  assets/media/manifest.json     maps original content-image URL -> local file

Idempotent: existing files are skipped, so re-running only fetches what is missing.
Gravatar and content-image capture need network access; blob avatars do not.

DB connection uses the same QA_DB_* environment variables as generate.py.
"""
import hashlib
import json
import re
import urllib.error
import urllib.request

from qa_common import (AVATARS, MEDIA, PREFIX, connect_with_retry, is_removed,
                       load_removed)

UA = "Mozilla/5.0 (OPF qanda archiver)"
QA_USER_FLAGS_SHOW_GRAVATAR = 8
_IMG_SRC = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)


def fetch(url, timeout=20):
    """Fetch a URL, returning (bytes, content_type). Raises on non-200."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise urllib.error.HTTPError(url, resp.status, "unexpected status",
                                         resp.headers, None)
        return resp.read(), (resp.headers.get("Content-Type") or "").lower()


def img_ext(data: bytes):
    """Real image extension from magic bytes, or None if this is not an image.

    URLs in a decade-old corpus often now serve HTML error pages, so the bytes
    decide whether we keep the response, not the URL or the Content-Type."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.lstrip()[:5].lower() == b"<?xml" or b"<svg" in data[:200].lower():
        return "svg"
    return None


def has_avatar(userid) -> bool:
    return any(AVATARS.glob(f"u{userid}.*"))


def blob_avatars(cur, removed):
    cur.execute(
        f"""SELECT u.userid, u.handle, b.content
            FROM {PREFIX}users u
            JOIN {PREFIX}blobs b ON b.blobid = u.avatarblobid
            WHERE u.avatarblobid IS NOT NULL
              AND u.userid IN (SELECT DISTINCT userid FROM {PREFIX}posts
                               WHERE type IN ('Q','A','C'))""")
    saved = skipped = 0
    for userid, handle, content in cur.fetchall():
        if has_avatar(userid) or not content or is_removed(removed, userid, handle):
            continue
        ext = img_ext(content)
        if ext is None:
            skipped += 1
            continue
        (AVATARS / f"u{userid}.{ext}").write_bytes(content)
        saved += 1
    return saved, skipped


def gravatars(cur, removed):
    """Fetch and cache the Gravatar of each contributor who chose one. Caching keeps
    the email hash out of the published HTML, which hotlinking would expose."""
    cur.execute(
        f"""SELECT DISTINCT u.userid, u.handle, u.email
            FROM {PREFIX}users u
            WHERE u.avatarblobid IS NULL
              AND (u.flags & {QA_USER_FLAGS_SHOW_GRAVATAR}) > 0
              AND u.email <> ''
              AND u.userid IN (SELECT DISTINCT userid FROM {PREFIX}posts
                               WHERE type IN ('Q','A','C'))""")
    saved = missing = 0
    for userid, handle, email in cur.fetchall():
        if has_avatar(userid) or is_removed(removed, userid, handle):
            continue
        digest = hashlib.md5(email.strip().lower().encode()).hexdigest()
        try:
            data, _ = fetch(f"https://www.gravatar.com/avatar/{digest}?s=200&d=404")
        except Exception:  # 404 means the address has no Gravatar
            missing += 1
            continue
        ext = img_ext(data)
        if ext is None:
            missing += 1
            continue
        (AVATARS / f"u{userid}.{ext}").write_bytes(data)
        saved += 1
    return saved, missing


def content_images(cur):
    cur.execute(f"SELECT content FROM {PREFIX}posts "
                f"WHERE type IN ('Q','A','C') AND content LIKE '%<img%'")
    urls = set()
    for (content,) in cur.fetchall():
        for src in _IMG_SRC.findall(content or ""):
            if src.startswith(("http://", "https://")):
                urls.add(src)

    manifest_path = MEDIA / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    saved = failed = 0
    for url in sorted(urls):
        if url in manifest and (MEDIA / manifest[url]).exists():
            continue
        try:
            data, _ = fetch(url)
        except Exception:
            failed += 1
            continue
        ext = img_ext(data)
        if ext is None:  # dead link now serving an HTML error page, etc.
            failed += 1
            continue
        name = f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.{ext}"
        (MEDIA / name).write_bytes(data)
        manifest[url] = name
        saved += 1

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return saved, failed, len(urls)


def main():
    AVATARS.mkdir(parents=True, exist_ok=True)
    MEDIA.mkdir(parents=True, exist_ok=True)
    removed = load_removed()
    conn = connect_with_retry()
    try:
        cur = conn.cursor()
        a_saved, a_skipped = blob_avatars(cur, removed)
        g_saved, g_missing = gravatars(cur, removed)
        c_saved, c_failed, c_total = content_images(cur)
    finally:
        conn.close()
    print(f"avatars: {a_saved} uploaded blobs, {g_saved} gravatars cached "
          f"({g_missing} had none, {a_skipped} non-image, "
          f"{len(removed)} on the removal list)")
    print(f"content images: {c_saved} mirrored, {c_failed} failed/not-an-image, "
          f"of {c_total} unique")


if __name__ == "__main__":
    main()
