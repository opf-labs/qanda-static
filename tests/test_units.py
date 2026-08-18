"""Unit tests for the pure helpers in the build scripts. No database needed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "build"))

import extract_media  # noqa: E402
import generate  # noqa: E402
import qa_common  # noqa: E402


class TestSlugify:
    @pytest.mark.parametrize("value,expected", [
        ("fixity", "fixity"),
        ("Web Archiving", "web-archiving"),
        ("C++", "c"),
        ("  spaced  out  ", "spaced-out"),
        ("!!!", "item"),
        ("", "item"),
        (None, "item"),
    ])
    def test_slugify(self, value, expected):
        assert generate.slugify(value) == expected

    def test_length_is_capped_without_trailing_dash(self):
        slug = generate.slugify("a" * 80)
        assert len(slug) == 60 and not slug.endswith("-")


class TestAllocate:
    def test_distinct_names_are_untouched(self):
        taken = set()
        assert [generate.allocate(n, taken) for n in ("a", "b")] == ["a", "b"]

    def test_collisions_get_numeric_suffixes(self):
        taken = set()
        got = [generate.allocate("c", taken) for _ in range(3)]
        assert got == ["c", "c-2", "c-3"]

    def test_reserved_names_are_avoided(self):
        assert generate.allocate("index", set(), reserved={"index"}) == "index-2"


class TestUserDir:
    @pytest.mark.parametrize("handle,expected", [
        ("grace", "grace"),
        ("Ada Lovelace", "Ada Lovelace"),
        ("foo/bar", "foo-bar"),
        ("foo\\bar", "foo-bar"),
        ("", "user"),
    ])
    def test_user_dir(self, handle, expected):
        assert generate.user_dir(handle) == expected

    def test_control_characters_are_stripped(self):
        assert generate.user_dir("ab\x01c") == "abc"


class TestCleanHtml:
    @pytest.mark.parametrize("raw", [
        '<script>alert(1)</script>',
        '<script>alert(1)',
        '<iframe src="//evil"></iframe>',
        '<object data="x"></object>',
        '<embed src="y">',
        '<form action="//evil"></form>',
        '<img src=x onerror=alert(1)>',
        '<a href="javascript:alert(1)">x</a>',
        '<a href=javascript:alert(1)>x</a>',
    ])
    def test_active_content_is_removed(self, raw):
        out = generate.clean_html(raw).lower()
        for bad in ("<script", "<iframe", "<object", "<embed", "<form",
                    "onerror", "javascript:"):
            assert bad not in out

    def test_ordinary_markup_survives(self):
        raw = '<p>Use <b>sha256sum</b> and <a href="https://example.invalid">this</a>.</p>'
        assert generate.clean_html(raw) == raw


class TestTextToHtml:
    def test_escapes_and_autolinks(self):
        out = generate.text_to_html("a <b> https://example.invalid/x\nsecond")
        assert "&lt;b&gt;" in out
        assert '<a href="https://example.invalid/x"' in out
        assert "<br>" in out


class TestImgExt:
    @pytest.mark.parametrize("data,expected", [
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 8, "jpg"),
        (b"GIF89a" + b"\x00" * 8, "gif"),
        (b"RIFF\x00\x00\x00\x00WEBP", "webp"),
    ])
    def test_recognises_image_formats(self, data, expected):
        assert extract_media.img_ext(data) == expected

    @pytest.mark.parametrize("data", [
        b"<!doctype html><html>404 not found</html>",
        b"not an image at all",
        b"",
    ])
    def test_rejects_non_images(self, data):
        assert extract_media.img_ext(data) is None


class TestRemovalList:
    def test_missing_file_means_nothing_removed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(qa_common, "REMOVED_LIST", tmp_path / "absent.txt")
        assert qa_common.load_removed() == set()

    def test_entries_are_parsed_and_comments_ignored(self, tmp_path, monkeypatch):
        f = tmp_path / "removed.txt"
        f.write_text("# a comment\n\n todrobbins  # requested\n42\nMiXeD\n")
        monkeypatch.setattr(qa_common, "REMOVED_LIST", f)
        assert qa_common.load_removed() == {"todrobbins", "42", "mixed"}

    @pytest.mark.parametrize("userid,handle,expected", [
        (42, "someone", True),
        (7, "todrobbins", True),
        (7, "TodRobbins", True),
        (7, "other", False),
    ])
    def test_is_removed_matches_id_or_handle(self, userid, handle, expected):
        removed = {"42", "todrobbins"}
        assert qa_common.is_removed(removed, userid, handle) is expected

    def test_empty_list_never_matches(self):
        assert qa_common.is_removed(set(), 1, "anyone") is False


class TestResolveAvatar:
    def test_returns_local_file_when_captured(self):
        got = generate.resolve_avatar(5, {"handle": "x"}, {5: "assets/avatars/u5.png"}, set())
        assert got == "assets/avatars/u5.png"

    def test_returns_none_when_no_avatar_captured(self):
        assert generate.resolve_avatar(5, {"handle": "x"}, {}, set()) is None

    def test_removed_contributor_is_never_rendered(self):
        files = {5: "assets/avatars/u5.png"}
        assert generate.resolve_avatar(5, {"handle": "x"}, files, {"5"}) is None
        assert generate.resolve_avatar(5, {"handle": "x"}, files, {"x"}) is None

    def test_no_email_hash_can_leak_into_output(self):
        """Avatars are local files only; a gravatar URL must never be produced."""
        got = generate.resolve_avatar(9, {"handle": "h"}, {9: "assets/avatars/u9.jpg"}, set())
        assert "gravatar" not in got
