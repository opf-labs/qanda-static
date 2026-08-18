"""End-to-end build against the synthetic fixture database.

Renders the whole site from tests/fixture.sql and asserts on the generated HTML.
Requires a MySQL/MariaDB reachable via the QA_DB_* variables; skipped otherwise,
so `pytest tests/test_units.py` still runs anywhere.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))

pymysql = pytest.importorskip("pymysql")


def _db_config():
    return dict(
        host=os.environ.get("QA_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("QA_DB_PORT", "3307")),
        user=os.environ.get("QA_DB_USER", "root"),
        password=os.environ.get("QA_DB_PASSWORD", "root"),
    )


@pytest.fixture(scope="module")
def fixture_db():
    """Load tests/fixture.sql into a scratch database, or skip if none is available."""
    cfg = _db_config()
    name = os.environ.get("QA_TEST_DB_NAME", "qanda_fixture")
    try:
        conn = pymysql.connect(connect_timeout=5, charset="utf8mb4", **cfg)
    except pymysql.Error as exc:
        pytest.skip(f"no database available: {exc}")
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {name}")
        cur.execute(f"CREATE DATABASE {name} CHARACTER SET utf8mb4")
    conn.close()

    conn = pymysql.connect(database=name, charset="utf8mb4", **cfg)
    sql = (ROOT / "tests" / "fixture.sql").read_text()
    with conn.cursor() as cur:
        for statement in [s.strip() for s in sql.split(";\n") if s.strip()]:
            if statement.lstrip().startswith("--"):
                lines = [ln for ln in statement.splitlines()
                         if not ln.lstrip().startswith("--")]
                statement = "\n".join(lines).strip()
            if statement:
                cur.execute(statement)
    conn.commit()
    conn.close()
    yield name


@pytest.fixture(scope="module")
def site(fixture_db, tmp_path_factory):
    """Run the real generator against the fixture and return the output directory."""
    out = tmp_path_factory.mktemp("site")
    env = {**os.environ, **{k.upper(): str(v) for k, v in ()}}
    cfg = _db_config()
    env.update(
        QA_DB_HOST=cfg["host"], QA_DB_PORT=str(cfg["port"]),
        QA_DB_USER=cfg["user"], QA_DB_PASSWORD=cfg["password"],
        QA_DB_NAME=fixture_db, QA_OUT_DIR=str(out), QA_ARCHIVED="January 2099",
    )
    result = subprocess.run([sys.executable, str(BUILD / "generate.py")],
                            capture_output=True, text=True, env=env, cwd=str(BUILD))
    assert result.returncode == 0, result.stderr
    return out


def read(site, *parts):
    return (site.joinpath(*parts)).read_text(encoding="utf-8")


class TestSiteStructure:
    def test_core_pages_exist(self, site):
        for page in ("index.html", "about.html", "tags/index.html"):
            assert (site / page).is_file(), page

    def test_one_directory_per_visible_question(self, site):
        assert {p.name for p in site.iterdir() if p.name.isdigit()} == {"10", "20", "30"}

    def test_queued_question_is_excluded(self, site):
        assert not (site / "40").exists()
        assert "Queued question" not in read(site, "index.html")

    def test_profile_per_contributor(self, site):
        users = {p.name for p in (site / "user").iterdir()}
        assert users == {"Ada Lovelace", "grace", "foo-bar", "foo-bar-2", "nomail"}

    def test_build_only_files_are_not_published(self, site):
        assert not (site / "assets" / "avatars" / "removed.txt").exists()
        assert not (site / "assets" / "media" / "manifest.json").exists()


class TestQuestionPages:
    def test_title_and_author_link(self, site):
        html = read(site, "10", "index.html")
        assert "How do I checksum a file?" in html
        assert 'href="../user/Ada%20Lovelace/"' in html

    def test_accepted_answer_is_marked_and_anchored(self, site):
        html = read(site, "10", "index.html")
        assert 'id="a11"' in html
        assert "accepted" in html
        assert "jump to accepted answer" in html

    def test_answers_and_comments_render(self, site):
        html = read(site, "10", "index.html")
        assert "sha256sum" in html
        assert "Agreed, sha256 is the safer default." in html

    def test_unanswered_question_links_to_answers_section(self, site):
        assert 'href="20/#answers"' in read(site, "index.html")

    def test_plain_text_urls_are_autolinked(self, site):
        assert '<a href="https://example.invalid/tool"' in read(site, "10", "index.html")


class TestSanitisation:
    def test_active_markup_never_reaches_output(self, site):
        html = read(site, "30", "index.html").lower()
        for bad in ("<script", "<iframe", "onerror=", "javascript:"):
            assert bad not in html

    def test_surrounding_content_survives(self, site):
        assert "<p>ok</p>" in read(site, "30", "index.html")


class TestCollisionHandling:
    def test_tags_sharing_a_slug_get_distinct_pages(self, site):
        pages = {p.name for p in (site / "tags").iterdir()}
        assert {"c.html", "c-2.html", "index.html", "index-2.html"} <= pages

    def test_tag_named_index_does_not_clobber_the_listing(self, site):
        assert "Tags" in read(site, "tags", "index.html")

    def test_colliding_handles_get_separate_profiles(self, site):
        pages = {d.name: read(site, "user", d.name, "index.html")
                 for d in (site / "user").iterdir() if d.name.startswith("foo-bar")}
        assert set(pages) == {"foo-bar", "foo-bar-2"}
        owners = {"<h1>foo/bar</h1>" in html for html in pages.values()}
        assert owners == {True, False}, "each colliding handle keeps its own profile"


class TestPrivacy:
    def test_no_contributor_email_addresses_anywhere(self, site):
        for path in site.rglob("*.html"):
            assert "@example.invalid" not in path.read_text(encoding="utf-8"), path

    def test_no_gravatar_requests(self, site):
        """Avatars are local copies, so no page may hand an email hash to Gravatar."""
        for path in site.rglob("*.html"):
            assert "gravatar.com" not in path.read_text(encoding="utf-8"), path

    def test_avatar_sources_are_all_local(self, site):
        for path in site.rglob("*.html"):
            html = path.read_text(encoding="utf-8")
            for tag in re.findall(r'<img[^>]*class="avatar[^>]*>', html):
                assert 'src="http' not in tag, f"{path}: remote avatar {tag}"


class TestContentImages:
    def test_unmirrored_image_keeps_its_original_source(self, site):
        """extract_media.py mirrors what it can reach; anything it could not fetch
        keeps the original URL rather than pointing at a file that does not exist."""
        html = read(site, "30", "index.html")
        assert 'src="https://example.invalid/pic.png"' in html


class TestProfilePages:
    def test_stats_come_from_userpoints(self, site):
        html = read(site, "user", "Ada Lovelace", "index.html")
        assert "350" in html
        assert "<h1>Ada Lovelace</h1>" in html

    def test_answer_links_back_to_its_question_anchor(self, site):
        assert 'href="../../10/#a11"' in read(site, "user", "grace", "index.html")
