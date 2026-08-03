"""Turn scraped event copy into a tiny, fixed HTML subset.

Sources write real documents: headings, bullet lists, emphasis. Flattening all
of that to a single string threw away the structure a reader uses to skim, but
forwarding the source markup would forward whatever else is on the page.

So nothing is forwarded. The parser reads the source HTML and *re-emits* a new
document from a fixed vocabulary — `p`, `h3`, `h4`, `ul`, `ol`, `li`, `strong`,
`em`, `br` — with **no attributes at all**. A tag outside the list contributes
its text and nothing else; `script`, `style` and their contents are dropped
wholesale. There is no path by which source markup reaches the output verbatim,
so there is no injection surface to reason about: the output is a construction,
not a filter.
"""

import re
from html import escape, unescape
from html.parser import HTMLParser

# What a source tag becomes. Headings collapse into two levels: the page owns
# its h1/h2, and event copy that competes with them breaks the document outline.
_TAG_MAP = {
    "p": "p",
    "h1": "h3",
    "h2": "h3",
    "h3": "h3",
    "h4": "h4",
    "h5": "h4",
    "h6": "h4",
    "ul": "ul",
    "ol": "ol",
    "li": "li",
    "strong": "strong",
    "b": "strong",
    "em": "em",
    "i": "em",
    "blockquote": "p",
    "dd": "p",
    "dt": "p",
    "br": "br",
}
_VOID = {"br"}
_BLOCK = {"p", "h3", "h4", "ul", "ol", "li"}
_CONTAINER = {"ul", "ol"}
_DROP_CONTENT = {"script", "style", "template", "noscript", "svg", "iframe"}

# A standalone bold line is how most municipal CMS templates write a subheading;
# only the short, non-sentence ones are treated that way.
_PSEUDO_HEADING_MAX_CHARS = 90


class _RichTextBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._open: list[str] = []
        self._skip_depth = 0

    # -- structure -----------------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP_CONTENT:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        mapped = _TAG_MAP.get(tag)
        if mapped == "br":
            if self._open:
                self._out.append("<br>")
            return
        if not mapped:
            return
        if mapped in _BLOCK:
            self._close_until_compatible(mapped)
        self._out.append(f"<{mapped}>")
        self._open.append(mapped)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP_CONTENT:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        mapped = _TAG_MAP.get(tag)
        if not mapped or mapped in _VOID:
            return
        if mapped in self._open:
            while self._open and self._open[-1] != mapped:
                self._out.append(f"</{self._open.pop()}>")
            self._out.append(f"</{self._open.pop()}>")

    def handle_startendtag(self, tag, attrs):
        if tag.lower() == "br" and not self._skip_depth and self._open:
            self._out.append("<br>")

    def handle_data(self, data):
        if self._skip_depth or not data.strip():
            # Whitespace between blocks is layout, not content; inside a block it
            # is a word separator and the block already ends with one.
            if data.strip() or not self._open:
                return
            self._out.append(" ")
            return
        if not self._open:
            self._out.append("<p>")
            self._open.append("p")
        self._out.append(escape(data, quote=False))

    def _close_until_compatible(self, mapped: str) -> None:
        """Close blocks that cannot contain the one starting now."""
        while self._open:
            current = self._open[-1]
            if mapped == "li" and current in _CONTAINER:
                return
            if current in _CONTAINER and mapped in _CONTAINER:
                return
            if current in _CONTAINER and mapped not in _BLOCK:
                return
            self._out.append(f"</{self._open.pop()}>")

    def result(self) -> str:
        while self._open:
            self._out.append(f"</{self._open.pop()}>")
        return "".join(self._out)


def _tidy(html: str) -> str:
    html = re.sub(r"[^\S\n]+", " ", html)
    # An empty block is a spacer the source used for layout.
    for _ in range(4):
        cleaned = re.sub(r"<(p|h3|h4|li|ul|ol)>\s*(?:<br>\s*)*</\1>", "", html)
        if cleaned == html:
            break
        html = cleaned
    html = re.sub(r"(?:<br>\s*)+</", "</", html)
    html = re.sub(r"<(p|h3|h4|li)>\s+", r"<\1>", html)
    html = re.sub(r"\s+</(p|h3|h4|li)>", r"</\1>", html)
    # Stripping an inline tag can leave a space before the punctuation it wrapped.
    html = re.sub(r" +([,.;:!?])", r"\1", html)
    return html.strip()


_BOLD_ONLY_PARAGRAPH = re.compile(rf"<p><strong>([^<]{{1,{_PSEUDO_HEADING_MAX_CHARS}}})</strong></p>")


def _promote_pseudo_headings(html: str) -> str:
    def replace(match: re.Match) -> str:
        text = match.group(1).strip()
        # A whole bold sentence is emphasis, not a section title, and a bold
        # date line ("30. Mai bis 30. August 2026") is a fact these templates
        # highlight rather than a section of the text.
        if not text or re.search(r"[.!]$", text) or len(text.split()) > 12:
            return match.group(0)
        if re.search(r"\b\d{4}\b|\d{1,2}\.\d{1,2}\.|\d{1,2}:\d{2}", text):
            return match.group(0)
        return f"<h3>{text}</h3>"

    return _BOLD_ONLY_PARAGRAPH.sub(replace, html)


def _top_level_blocks(html: str) -> list[str]:
    return [match.group(0) for match in re.finditer(r"<(p|h3|h4|ul|ol)>.*?</\1>", html, re.S)]


def text_length(html: str) -> int:
    """Visible characters, which is what a length budget is really about."""
    return len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip())


def sanitize_rich_text(value: str, max_chars: int | None = None) -> str:
    """Return event copy as the allowed subset, trimmed to whole blocks."""
    if not value:
        return ""
    builder = _RichTextBuilder()
    builder.feed(value)
    html = _promote_pseudo_headings(_tidy(builder.result()))
    if not max_chars or text_length(html) <= max_chars:
        return html

    # Cut between blocks, never inside one: a half-open list or a sentence that
    # stops mid-clause looks broken in a way a shorter text does not.
    kept: list[str] = []
    for block in _top_level_blocks(html):
        candidate = "".join([*kept, block])
        if kept and text_length(candidate) > max_chars:
            break
        kept.append(block)
        if text_length("".join(kept)) >= max_chars:
            break
    return "".join(kept)


def to_plain_text(html: str) -> str:
    """The same copy as paragraph-separated plain text."""
    text = re.sub(r"</(p|h3|h4|li|ul|ol)>", "\n\n", html or "")
    text = re.sub(r"<br>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def from_plain_text(text: str) -> str:
    """Wrap already-flattened copy so every event has renderable markup.

    Sources whose extractor still hands over plain text keep their paragraph
    breaks; they simply gain no headings or lists, because that structure was
    lost before this point rather than being withheld here.
    """
    paragraphs = [block.strip() for block in re.split(r"\n{2,}", text or "") if block.strip()]
    return "".join(
        "<p>" + "<br>".join(escape(line, quote=False) for line in block.split("\n")) + "</p>" for block in paragraphs
    )


def _comparison_key(text: str) -> str:
    """Letters and digits only, so punctuation and breaks cannot cause a mismatch."""
    return re.sub(r"[^0-9a-zà-öø-ÿ]+", "", (text or "").casefold())


def describes_same_copy(html: str, text: str, *, probe: int = 60) -> bool:
    """Whether this markup is still the rendering of ``text``.

    ``make_event`` seeds the markup from whatever description it was handed, and
    several sources replace that description afterwards with richer detail-page
    copy. The markup then silently describes the older text — for one event the
    page showed a generated "findet am … statt" line while the description held
    the real 1 200-character write-up. The two forms may differ in length and in
    structure, so only their opening is compared.
    """
    rendered, plain = _comparison_key(to_plain_text(html)), _comparison_key(text)
    if not rendered or not plain:
        return not rendered and not plain
    head = min(probe, len(rendered), len(plain))
    return rendered[:head] == plain[:head]
