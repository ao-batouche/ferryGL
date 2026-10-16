#!/usr/bin/env python3
"""Build the ferryGL website into _site/.

The site has two parts:

- the pages in site/: the home page, its partials, the stylesheet, the script and images;
- the documentation in docs/*.md, rendered with GitHub's Markdown API so that it reads the
  same as on GitHub. docs/README.md becomes the first page, and the order of its links to the
  other pages sets the order of the sidebar.

Build and preview:

    python3 site/build.py
    python3 -m http.server --directory _site

Without a token, GitHub's Markdown API allows 60 requests an hour, enough for several builds.
Set GH_TOKEN to raise that.

Templates have two kinds of placeholder: <!-- include: _name.html --> inserts a partial from
site/, and {{name}} inserts a value, such as {{root}}, the path from the page to the site
root. Files in site/ whose names start with "_" are partials and aren't published.

The build fails on a link to a page or anchor that doesn't exist, so renaming a heading in
docs/ can't silently break a link to it.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = "https://github.com/ao-batouche/ferryGL"
BRANCH = "main"
SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
MARKDOWN_API = "https://api.github.com/markdown"

INCLUDE = re.compile(r"<!-- include: (_[\w-]+\.html) -->")
VALUE = re.compile(r"\{\{(\w+)\}\}")
# How GitHub's Markdown API renders a heading, with the anchor GitHub itself would use.
HEADING = re.compile(
    r'<div class="markdown-heading"><h([1-6]) class="heading-element">(.*?)</h\1>'
    r'<a id="user-content-([^"]+)" class="anchor"[^>]*>.*?</a></div>',
    re.S,
)
TAG = re.compile(r"<[^>]+>")
URL_ATTR = re.compile(r'\b(href|src)="([^"]*)"')


class BuildError(Exception):
    """The site can't be built as it stands."""


@dataclass
class DocPage:
    source: Path
    slug: str  # the output name without ".html": "index" for docs/README.md
    title: str = ""
    description: str = ""
    body: str = ""
    outline: list[tuple[int, str, str]] = field(default_factory=list)  # (level, id, text)

    @property
    def filename(self) -> str:
        return f"{self.slug}.html"

    @property
    def label(self) -> str:
        return "Overview" if self.slug == "index" else self.title


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the ferryGL website.")
    parser.add_argument("--out", type=Path, default=ROOT / "_site", help="default: _site")
    parser.add_argument("--docs", type=Path, default=ROOT / "docs", help="default: docs")
    parser.add_argument(
        "--base-url",
        default="",
        help="the published site's URL, such as https://owner.github.io/ferryGL, "
        "for social previews and the 404 page",
    )
    args = parser.parse_args(argv)
    try:
        count = build(args.out.resolve(), args.docs.resolve(), args.base_url.rstrip("/"))
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Built {count} pages in {args.out}")
    return 0


def build(out: Path, docs: Path, base_url: str) -> int:
    for kept in (SITE, docs):
        if kept.is_relative_to(out) or out.is_relative_to(kept):
            raise BuildError(f"refusing to replace {out}: it holds the site's sources")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    pages = load_docs(docs)
    for page in pages:
        render_page(page, docs, token)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    count = publish_site(out, base_url)
    template = (SITE / "_doc.html").read_text("utf-8")
    (out / "docs").mkdir(exist_ok=True)
    for i, page in enumerate(pages):
        values = {
            "root": "../",
            "site_url": f"{base_url}/" if base_url else "../",
            "page_title": html.escape(
                page.title if page.slug == "index" else f"{page.title} · ferryGL documentation"
            ),
            "description": html.escape(page.description),
            "nav": nav(pages, page),
            "toc": toc(page),
            "content": page.body,
            "pager": pager(pages, i),
            "edit_url": f"{REPO}/edit/{BRANCH}/docs/{page.source.name}",
        }
        (out / "docs" / page.filename).write_text(fill(template, values), "utf-8")
    check_links(out)
    return count + len(pages)


def load_docs(docs: Path) -> list[DocPage]:
    """docs/README.md first, then the pages it links to in order, then any others."""
    index = docs / "README.md"
    if not index.is_file():
        raise BuildError(f"{index} not found: the documentation needs an index page")
    order: list[str] = []
    for name in re.findall(r"\]\(([\w.-]+\.md)(?:#[^)\s]*)?\)", index.read_text("utf-8")):
        if name != "README.md" and name not in order:
            if not (docs / name).is_file():
                raise BuildError(f"{index} links to {name}, which doesn't exist")
            order.append(name)
    rest = sorted(p.name for p in docs.glob("*.md") if p.name not in {"README.md", *order})
    return [DocPage(index, "index")] + [DocPage(docs / n, Path(n).stem) for n in order + rest]


def render_page(page: DocPage, docs: Path, token: str | None) -> None:
    text = render_markdown(page.source.read_text("utf-8"), token)

    def heading(match: re.Match[str]) -> str:
        level, inner, anchor = int(match[1]), match[2], match[3]
        plain = html.escape(html.unescape(TAG.sub("", inner)).strip())
        if level == 1:
            page.title = page.title or html.unescape(TAG.sub("", inner)).strip()
            return f'<h1 id="{anchor}">{inner}</h1>'
        if level <= 3:
            page.outline.append((level, anchor, plain))
        link = f'<a class="anchor" href="#{anchor}" aria-label="Link to this section"></a>'
        return f'<h{level} id="{anchor}">{inner}{link}</h{level}>'

    text = HEADING.sub(heading, text)
    if re.search(r"<h[1-6](?![^>]*\bid=)", text):
        raise BuildError(
            f"{page.source.name}: a heading has no anchor. GitHub's Markdown output may have "
            "changed; update HEADING in site/build.py"
        )
    if not page.title:
        raise BuildError(f"{page.source.name}: start the page with a '# Title' heading")

    text = URL_ATTR.sub(lambda m: f'{m[1]}="{html.escape(link_target(m, docs))}"', text)
    text = re.sub(r"</?markdown-accessiblity-table>", "", text)
    text = re.sub(r"<table\b[^>]*>", '<div class="table-wrap"><table>', text)
    text = text.replace("</table>", "</table></div>")
    # Plain code blocks get the same frame, and copy button, as highlighted ones.
    text = re.sub(r'<pre(?: lang="[^"]*")?><code>', '<div class="highlight"><pre><code>', text)
    text = text.replace("</code></pre>", "</code></pre></div>")
    page.body = text

    first = re.search(r"<p>(.*?)</p>", text, re.S)
    summary = " ".join(html.unescape(TAG.sub("", first[1])).split()) if first else page.title
    if len(summary) > 160:
        summary = summary[:157].rsplit(" ", 1)[0] + "…"
    page.description = summary


def render_markdown(text: str, token: str | None) -> str:
    """HTML for ``text``, as GitHub renders a Markdown file in a repository."""
    headers = {
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "ferrygl-site-build",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps({"text": text, "mode": "markdown"}).encode()
    for attempt in range(3):
        request = urllib.request.Request(MARKDOWN_API, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429) and not token:
                raise BuildError(
                    "GitHub's Markdown API rate limit reached; set GH_TOKEN and try again"
                ) from None
            if exc.code < 500 or attempt == 2:
                detail = exc.read().decode("utf-8", "replace")[:200]
                raise BuildError(
                    f"GitHub's Markdown API returned HTTP {exc.code}: {detail}"
                ) from None
        except urllib.error.URLError as exc:
            if attempt == 2:
                raise BuildError(f"couldn't reach GitHub's Markdown API: {exc.reason}") from None
        time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def link_target(match: re.Match[str], docs: Path) -> str:
    """Where a link in the docs should point on the site."""
    attr, url = match[1], html.unescape(match[2])
    parts = urlsplit(url)
    if parts.scheme or parts.netloc or not parts.path:  # another site, or this page
        return url
    anchor = f"#{parts.fragment}" if parts.fragment else ""
    target = (docs / unquote(parts.path)).resolve()
    if target.parent == docs and target.suffix == ".md":
        return ("index" if target.name == "README.md" else target.stem) + ".html" + anchor
    # Anything else in the repository, such as ../AGENTS.md, is linked on GitHub.
    if not target.is_relative_to(docs.parent):
        raise BuildError(f"docs link to {url} points outside the repository")
    path = target.relative_to(docs.parent).as_posix()
    kind = "raw" if attr == "src" else "tree" if target.is_dir() else "blob"
    return f"{REPO}/{kind}/{BRANCH}/{path}{anchor}"


def publish_site(out: Path, base_url: str) -> int:
    """Copy site/ to ``out``, filling in the HTML pages. Returns the number of pages."""
    pages = 0
    for path in sorted(SITE.rglob("*")):
        rel = path.relative_to(SITE)
        if path.is_dir() or path == Path(__file__).resolve() or "__pycache__" in rel.parts:
            continue
        if any(part.startswith(("_", ".")) for part in rel.parts):
            continue
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix != ".html":
            shutil.copy2(path, target)
            continue
        root = "../" * (len(rel.parts) - 1) or "./"
        if rel.as_posix() == "404.html":
            # GitHub Pages serves it for missing pages at any depth, so its links are absolute.
            root = urlsplit(base_url).path + "/"
        values = {"root": root, "site_url": f"{base_url}/" if base_url else root}
        target.write_text(fill(path.read_text("utf-8"), values), "utf-8")
        pages += 1
    return pages


def fill(text: str, values: dict[str, str]) -> str:
    for _ in range(5):
        text, count = INCLUDE.subn(lambda m: partial(m[1]), text)
        if not count:
            break
    else:
        raise BuildError("partials include each other too deeply")

    def value(match: re.Match[str]) -> str:
        if match[1] not in values:
            raise BuildError(f"no value for the placeholder {{{{{match[1]}}}}}")
        return values[match[1]]

    # One pass, so placeholders that happen to appear in the docs' text are left alone.
    return VALUE.sub(value, text)


def partial(name: str) -> str:
    path = SITE / name
    if not path.is_file():
        raise BuildError(f"the partial {name} doesn't exist in {SITE}")
    return path.read_text("utf-8").rstrip("\n")


def nav(pages: list[DocPage], current: DocPage) -> str:
    items = []
    for page in pages:
        mark = ' aria-current="page"' if page is current else ""
        label = html.escape(page.label)
        items.append(f'      <li><a href="{page.filename}"{mark}>{label}</a></li>')
    return "\n".join(items)


def toc(page: DocPage) -> str:
    if not page.outline:
        return ""
    items = [
        f'      <li class="toc-{level}"><a href="#{anchor}">{text}</a></li>'
        for level, anchor, text in page.outline
    ]
    return "\n".join(["    <h2>On this page</h2>", "    <ul>", *items, "    </ul>"])


def pager(pages: list[DocPage], i: int) -> str:
    links = []
    if i > 0:
        prev = pages[i - 1]
        links.append(
            f'<a class="prev" href="{prev.filename}"><span>Previous</span>'
            f"{html.escape(prev.label)}</a>"
        )
    if i + 1 < len(pages):
        after = pages[i + 1]
        links.append(
            f'<a class="next" href="{after.filename}"><span>Next</span>'
            f"{html.escape(after.label)}</a>"
        )
    if not links:
        return ""
    return f'    <nav class="pager" aria-label="Previous and next pages">{"".join(links)}</nav>'


def check_links(out: Path) -> None:
    """Fail on links to files or anchors that don't exist in the built site."""
    anchors: dict[Path, set[str]] = {}

    def ids(path: Path) -> set[str]:
        if path not in anchors:
            anchors[path] = set(re.findall(r'\bid="([^"]+)"', path.read_text("utf-8")))
        return anchors[path]

    problems = []
    for page in sorted(out.rglob("*.html")):
        if page.name == "404.html":
            continue  # its links are absolute
        for _, raw in URL_ATTR.findall(page.read_text("utf-8")):
            url = html.unescape(raw)
            parts = urlsplit(url)
            if parts.scheme or parts.netloc or url.startswith("/"):
                continue
            target = (page.parent / unquote(parts.path)).resolve() if parts.path else page
            if target.is_dir():
                target = target / "index.html"
            where = page.relative_to(out).as_posix()
            if not target.is_file():
                problems.append(f"{where}: {url} doesn't exist")
            elif (
                parts.fragment
                and target.suffix == ".html"
                and unquote(parts.fragment) not in ids(target)
            ):
                problems.append(f"{where}: {url} has no such anchor")
    if problems:
        raise BuildError("broken links:\n  " + "\n  ".join(problems))


if __name__ == "__main__":
    sys.exit(main())
