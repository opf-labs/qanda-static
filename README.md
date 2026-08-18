# qanda-static

Generator for a read-only static archive of **qanda.digipres.org**, the Open
Preservation Foundation community Q&A. The original ran on
[Question2Answer](https://www.question2answer.org/) 1.6.2, a PHP application from 2013,
on a Debian 7 server being decommissioned. This tool renders that site's database into
plain HTML and packages it as a container image, so the content survives without the
application, its attack surface, or the registration spam it accumulated.

Styled to match the OPF `wiki-static` property: Powered-by-OPF bar, brand footer, and a
banner on every page marking it as an archived copy.

## This repository contains code, not content

**No archive content is stored here, by design.**

| Not in git | Why | Where it lives |
|---|---|---|
| The database dump | Holds ~14.7k contributor email addresses and password salts/hashes | Build server only |
| `site/` (generated HTML) | Content in git history could never be withdrawn, which would defeat removal requests | Built on demand |
| `assets/avatars/`, `assets/media/` | Contributor photographs and post images, derived from the private database | Build server only |
| `assets/avatars/removed.txt` | Names the people who exercised a privacy request | Build server only |
| `.env` | Cloudflare Tunnel token | Build server only |

Because nothing is committed, honouring a removal request is simply a rebuild: the
picture is gone from every artefact, with no history to scrub. See
**[Handling a removal request](#handling-a-removal-request-site-administrators)**.

## Building the archive

Requires Docker and a copy of the database dump. On the build server:

```bash
./build.sh /path/to/qanda-YYYY-MM-DD.sql.gz            # -> qanda-static:latest
./build.sh /path/to/qanda-YYYY-MM-DD.sql.gz myorg/qanda-static:2026-08
```

That script:

1. starts a throwaway MariaDB and loads the dump,
2. runs `build/extract_media.py` to capture avatars and post images into `assets/`,
3. runs `build/generate.py` to render `site/`,
4. destroys the database,
5. packages `site/` into a self-contained nginx image via `Dockerfile.serve`.

Run the result anywhere:

```bash
docker run --rm -p 8088:80 qanda-static:latest
```

The image is the only artefact that carries archive content. It needs no database and
makes no third-party requests.

### Running stages by hand

```bash
export QANDA_DUMP=/abs/path/to/dump.sql.gz HOST_UID=$(id -u) HOST_GID=$(id -g)
docker compose --profile build run --rm --build extract   # images into ./assets
docker compose --profile build up --build generate        # render ./site
docker compose --profile build down -v
docker compose up -d web                                  # preview on :8088
```

`extract` is idempotent, so re-running only fetches what is missing. `generate` depends
on `extract` completing. Both run as `HOST_UID`/`HOST_GID` so output is not root-owned.

Without Docker, against any reachable MySQL/MariaDB holding the dump:

```bash
pip install -r build/requirements.txt
QA_DB_HOST=127.0.0.1 QA_DB_PORT=3307 QA_DB_USER=root QA_DB_PASSWORD=root \
  python3 build/extract_media.py && python3 build/generate.py
```

Environment variables: `QA_DB_HOST/PORT/USER/PASSWORD/NAME`, `QA_TABLE_PREFIX`
(default `qa_`), `QA_OUT_DIR` (default `site/`), `QA_ARCHIVED` (banner date string).

## What the generator produces

From the August 2026 dump: 133 questions, 234 answers, 193 comments, 172 tags and 118
contributor profiles, spanning 2011 to 2024, with original vote counts, authors and
dates. Excluded: the ~14.7k spam registrations, all account data, and every interactive
feature.

Question2Answer behaviours reproduced:

- **Answer counts jump to the accepted answer.** Green when an answer was accepted,
  linking to `/<id>/#a<answerid>`; otherwise to the answers section.
- **Author names link to profiles** at `/user/<handle>/`, showing points, post counts,
  member-since date and the person's questions and answers.
- **Question2Answer URLs still work**, so this can replace the live service in place:

  | Old link | Resolves to |
  |---|---|
  | `/<id>` | 301 to `/<id>/` |
  | `/<id>/<any-old-slug>` | the question (slug-independent) |
  | `/user/<handle>` | the contributor profile |
  | `/tag/<tag>` | the tag page |
  | `/questions` | the home page |

### Images

All images are captured locally, so the published site makes no third-party requests:
uploaded avatars from the Q2A blob store, Gravatars fetched once by email hash, and
images embedded in post content.

Gravatars are **cached rather than hotlinked** deliberately. A Gravatar URL contains the
MD5 of the contributor's email address, so hotlinking would publish a reversible form of
an address the archive otherwise withholds. Caching keeps that hash out of the HTML
entirely. The trade-off: cached pictures are frozen as at capture.

An image the extractor cannot fetch keeps its original external URL rather than pointing
at a file that does not exist.

## Handling a removal request (site administrators)

Requests come to **info@openpreservation.org**; the archive's About page tells people to
write there. Removals are permanent: the opt-out list is build input, so a removed
picture stays removed through every future rebuild.

**To remove someone's profile picture:**

1. Identify them. The handle is in the profile URL, e.g. `/user/todrobbins/` -> handle
   `todrobbins`. Their picture is `assets/avatars/u<userid>.<ext>`; find the userid with
   `grep -r 'assets/avatars/u' site/user/<handle>/index.html`.
2. On the build server, add the handle (or numeric userid) to
   **`assets/avatars/removed.txt`** (create it from `removed.txt.example`), one entry per
   line, with a dated comment:

   ```
   todrobbins        # requested 2026-08-18
   ```
3. Delete the cached file: `rm assets/avatars/u<userid>.*`
4. Rebuild and redeploy: `./build.sh /path/to/dump.sql.gz` then push the new image.

The entry stops `extract_media.py` re-fetching the picture and stops `generate.py`
rendering it, so step 3 cannot be silently undone by a later build. The person's posts,
profile page and attribution are untouched; only the picture disappears. `removed.txt` is
never copied into the generated site, so the list of requesters is not published.

**To remove an embedded post image**, delete it from `assets/media/` and its entry from
`assets/media/manifest.json`, then rebuild. The post falls back to its original external
URL, so if the image must not appear at all, edit the post body in the database too.

**To restore a picture**, delete the line from `removed.txt` and rebuild.

## Deploying

Serve the image behind a Cloudflare Tunnel, as `wiki-static` does. The repo ships a
token-based `cloudflared` sidecar (compose `tunnel` profile), so no credentials files are
stored and no inbound ports are opened.

1. Cloudflare Zero Trust: **Networks -> Tunnels -> Create a tunnel**, copy the token.
2. Add a **Public Hostname**: your hostname -> Service **HTTP** -> `http://web:80`.
3. `cp .env.example .env` and paste the token into `CLOUDFLARE_TUNNEL_TOKEN`.
4. `docker compose --profile tunnel up -d`, then
   `docker compose logs -f cloudflared` to confirm it registers.

`.env` is gitignored. Any static host works as an alternative; the output has no
server-side dependencies.

## Tests

```bash
pip install -r build/requirements.txt -r tests/requirements.txt
pytest tests/test_units.py     # pure helpers, no database
pytest                          # adds an end-to-end build (needs a database)
```

`tests/fixture.sql` is a small **synthetic** database - no real archive data - covering
the awkward cases: tags that reduce to the same slug, a tag named `index`, handles that
collide as directory names, a handle with a space, active markup that must be sanitised,
an accepted answer, comments, an uploaded avatar and a Gravatar user. CI runs both suites
against it on every push, and checks the serving image refuses to build without a
generated site.

## Layout

```
build/
  generate.py          renders site/ from the database
  extract_media.py     captures avatars and post images into assets/
  qa_common.py         shared DB config, connect-with-retry, removal list
  templates/           Jinja2 templates
  Dockerfile           generator image
assets/
  qanda.css            OPF branding and Q&A styles
  opf/                 OPF logo and banner background
  avatars/removed.txt.example
build.sh               dump -> site -> container image
Dockerfile.serve       packages a generated site into nginx
docker-compose.yml     build, preview and tunnel profiles
nginx.conf             legacy Q2A URL rewrites
tests/                 unit tests and a synthetic-fixture end-to-end build
```

## Known caveats

- A handful of the earliest posts (around Dec 2013) were bulk-imported under one account,
  so their attribution shows the importer's handle while the original contributor's name
  remains inline in the text. Attribution is otherwise faithful to the database.
- One question sat in the moderation queue (`Q_QUEUED`) and is intentionally excluded.
- Cached Gravatars are frozen at capture; later changes upstream are not picked up.

## Licence

Archive content carries the CC BY-SA 4.0 notice shown in the site footer, as on the
original site. No licence has been set for the generator code yet - add a `LICENSE` file
to settle it.
