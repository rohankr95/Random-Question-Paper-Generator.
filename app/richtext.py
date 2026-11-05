"""Rich text (formatted HTML) support for question text and answers.

Questions written in the editor are stored as *sanitised* HTML: only a small
allow-list of formatting tags survives (bold, italic, lists, tables, ...).
Anything else - scripts, links, images, event handlers, arbitrary styles - is
removed on the server, so one user can never inject code into another user's
browser.

Older questions (and CSV imports) may be plain text. Those are detected and
shown with their line breaks preserved, so both kinds can live side by side.
"""
import html
import re

import nh3
from markupsafe import Markup, escape

ALLOWED_TAGS = {
    "p", "br", "div", "span", "hr", "blockquote", "pre", "code",
    "b", "strong", "i", "em", "u", "s", "strike", "sub", "sup", "mark", "small",
    "ul", "ol", "li",
    "table", "caption", "colgroup", "col", "thead", "tbody", "tfoot", "tr", "th", "td",
    "h3", "h4",
}
_ALIGNABLE = ("p", "div", "span", "td", "th", "table", "li", "h3", "h4", "blockquote")
ALLOWED_ATTRIBUTES = {tag: {"style"} for tag in _ALIGNABLE}
ALLOWED_ATTRIBUTES["td"] |= {"colspan", "rowspan"}
ALLOWED_ATTRIBUTES["th"] |= {"colspan", "rowspan", "scope"}
ALLOWED_ATTRIBUTES["col"] = {"span"}
ALLOWED_ATTRIBUTES["colgroup"] = {"span"}
ALLOWED_ATTRIBUTES["ol"] = {"start", "type"}
ALLOWED_STYLES = {
    "text-align", "vertical-align", "font-weight", "font-style",
    "text-decoration", "text-decoration-line",
}

_TAG_RE = re.compile(
    r"<\s*/?\s*(" + "|".join(sorted(ALLOWED_TAGS, key=len, reverse=True)) + r")(\s[^<>]*)?/?\s*>",
    re.IGNORECASE,
)
_BLOCK_END_RE = re.compile(r"<\s*(br\s*/?|/(p|div|li|tr|h3|h4|blockquote|pre|caption))\s*>", re.IGNORECASE)
_CELL_END_RE = re.compile(r"<\s*/(td|th)\s*>", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]*>")
_EMPTY_STYLE_RE = re.compile(r'\sstyle=""')


def is_html(text):
    """True if the text contains at least one real formatting tag (e.g. <b>, <table>)."""
    return bool(text) and bool(_TAG_RE.search(text))


def sanitize(text):
    cleaned = nh3.clean(
        text,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        filter_style_properties=ALLOWED_STYLES,
        url_schemes=set(),
        strip_comments=True,
    )
    return _EMPTY_STYLE_RE.sub("", cleaned).strip()


def to_plain(text):
    """Plain-text version (for duplicate checks, length checks and short previews)."""
    if not text:
        return ""
    if not is_html(text):
        return text
    text = _CELL_END_RE.sub(" \t", text)
    text = _BLOCK_END_RE.sub("\n", text)
    text = _ANY_TAG_RE.sub("", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def clean_input(text):
    """Normalise what a user submitted: sanitise HTML, keep plain text as-is.

    Returns "" when there is no visible content (e.g. an empty editor's "<p><br></p>").
    """
    text = (text or "").strip()
    if not text:
        return ""
    if is_html(text):
        text = sanitize(text)
        if not to_plain(text).strip() and "<table" not in text.lower():
            return ""
    return text


def render(text):
    """HTML that is safe to put on a page, for either stored format."""
    if not text:
        return Markup("")
    if is_html(text):
        return Markup(sanitize(text))
    return Markup("<br>").join(escape(line) for line in text.split("\n"))
