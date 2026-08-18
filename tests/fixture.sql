-- Synthetic fixture for CI. Contains NO real archive data: every user, post and
-- image here is invented. Mirrors just enough of the Question2Answer 1.6.2 schema
-- for the build scripts, and deliberately includes awkward cases:
--   * two tags that reduce to the same slug            (C, C++)
--   * a tag that would collide with the tag index page (index)
--   * two handles that reduce to the same directory    (foo/bar, foo-bar)
--   * a handle containing a space                      (Ada Lovelace)
--   * active markup that must be sanitised             (script/iframe/onerror)
--   * an accepted answer, comments, an uploaded avatar and a gravatar user

DROP TABLE IF EXISTS qa_posts, qa_users, qa_userpoints, qa_blobs;

CREATE TABLE qa_blobs (
  blobid BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  format VARCHAR(20) NOT NULL,
  content MEDIUMBLOB,
  filename VARCHAR(255), userid INT UNSIGNED, cookieid BIGINT UNSIGNED,
  createip INT UNSIGNED, created DATETIME
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE qa_users (
  userid INT UNSIGNED NOT NULL PRIMARY KEY,
  created DATETIME NOT NULL,
  email VARCHAR(80) NOT NULL DEFAULT '',
  handle VARCHAR(40) NOT NULL,
  avatarblobid BIGINT UNSIGNED NULL,
  level TINYINT UNSIGNED NOT NULL DEFAULT 0,
  flags SMALLINT UNSIGNED NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE qa_userpoints (
  userid INT UNSIGNED NOT NULL PRIMARY KEY,
  points INT NOT NULL DEFAULT 0,
  qposts MEDIUMINT NOT NULL DEFAULT 0,
  aposts MEDIUMINT NOT NULL DEFAULT 0,
  cposts MEDIUMINT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE qa_posts (
  postid INT UNSIGNED NOT NULL PRIMARY KEY,
  type ENUM('Q','A','C','Q_HIDDEN','A_HIDDEN','C_HIDDEN','Q_QUEUED','A_QUEUED','C_QUEUED','NOTE') NOT NULL,
  parentid INT UNSIGNED NULL,
  selchildid INT UNSIGNED NULL,
  userid INT UNSIGNED NULL,
  name VARCHAR(40) NULL,
  acount SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  netvotes SMALLINT NOT NULL DEFAULT 0,
  views INT UNSIGNED NOT NULL DEFAULT 0,
  format VARCHAR(20) NOT NULL DEFAULT '',
  created DATETIME NOT NULL,
  title VARCHAR(800) NULL,
  content VARCHAR(8000) NULL,
  tags VARCHAR(800) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO qa_blobs (blobid, format, content, created) VALUES
  (900, 'png', 0x89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082, '2014-01-01 10:00:00');

INSERT INTO qa_users (userid, created, email, handle, avatarblobid, level, flags) VALUES
  (1, '2013-01-05 09:00:00', 'ada@example.invalid',    'Ada Lovelace', 900, 100, 1),
  (2, '2013-02-06 09:00:00', 'grace@example.invalid',  'grace',       NULL, 0,   9),
  (3, '2013-03-07 09:00:00', 'alan@example.invalid',   'foo/bar',     NULL, 0,   1),
  (4, '2013-04-08 09:00:00', 'edsger@example.invalid', 'foo-bar',     NULL, 0,   1),
  (5, '2013-05-09 09:00:00', '',                       'nomail',      NULL, 0,   9);

INSERT INTO qa_userpoints (userid, points, qposts, aposts, cposts) VALUES
  (1, 350, 2, 1, 1), (2, 120, 1, 1, 0), (3, 10, 0, 1, 0), (4, 5, 0, 1, 0), (5, 0, 1, 0, 0);

INSERT INTO qa_posts (postid, type, parentid, selchildid, userid, acount, netvotes, views, format, created, title, content, tags) VALUES
  (10, 'Q', NULL, 11, 1, 2, 7, 500, '',     '2014-01-10 10:00:00', 'How do I checksum a file?', 'Plain text question with a link https://example.invalid/tool and a line.', 'fixity,C'),
  (11, 'A', 10,   NULL, 2, 0, 4, 0,  'html', '2014-01-11 10:00:00', NULL, '<p>Use <b>sha256sum</b>.</p>', NULL),
  (12, 'A', 10,   NULL, 3, 0, 1, 0,  '',     '2014-01-12 10:00:00', NULL, 'Or md5, but it is weak.', NULL),
  (13, 'C', 10,   NULL, 4, 0, 0, 0,  '',     '2014-01-13 10:00:00', NULL, 'Agreed, sha256 is the safer default.', NULL),
  (20, 'Q', NULL, NULL, 1, 0, 2, 120, '',    '2014-02-10 10:00:00', 'Unanswered question about tape', 'Nobody replied to this one.', 'C++,index'),
  (30, 'Q', NULL, NULL, 5, 1, 0, 9,  'html', '2014-03-10 10:00:00', 'Question with active markup', '<p>ok</p><script>alert(1)</script><iframe src="//evil"></iframe><img src=x onerror=alert(1)><a href=javascript:alert(2)>x</a><img src="https://example.invalid/pic.png">', 'security'),
  (31, 'A', 30,  NULL, 2, 0, 0, 0,  '',     '2014-03-11 10:00:00', NULL, 'Answer to the markup question.', NULL),
  (40, 'Q_QUEUED', NULL, NULL, 1, 0, 0, 0, '', '2014-04-10 10:00:00', 'Queued question that must be skipped', 'Should not appear.', 'moderation');
