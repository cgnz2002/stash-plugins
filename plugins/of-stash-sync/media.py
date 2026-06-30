"""Turn OF-Scraper post text and media rows into Stash metadata.

Title/details handling matches the original ofscraper-stash-sync tool. The only
behavioural change is that the third-party `emojis` dependency is replaced with
a small standard-library emoji detector so the plugin stays dependency-free.
"""

import html
import os
import re
from datetime import datetime

# Broad set of emoji/pictograph/dingbat/flag ranges, used to allow titles to be
# truncated immediately after an emoji (mirrors the original tool's behaviour).
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols, pictographs, supplemental, extended-A
    "\U00002600-\U000027BF"  # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators (flags)
    "\U00002B00-\U00002BFF"  # misc symbols and arrows
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U00002190-\U000021FF"  # arrows
    "]",
    flags=re.UNICODE,
)

# @mentions: allows an html tag before the name (e.g. <a href=''>@name</a>),
# periods and dashes inside the name, and ignores trailing punctuation.
_MENTION_RE = re.compile(
    r"(?:^|\s|>)@([\w\-]+(?:\.[\w\-]+)*)(?=[\s\.\?\!…<,:;]|$)"
)

_BR_RE = re.compile(r"<br\s*/?>", flags=re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")

# Separator/boundary patterns mirroring Stash's auto-tag matcher
# (pkg/match/path.go): a space in a name matches any run of separator
# characters (or none), and matches must fall on word boundaries.
_NAME_SEPARATOR = r"[.\-_ ]"
_NAME_NOT_WORD = r"[^\w\d]"


def compile_name_pattern(name):
    """Compile a regex that matches `name` in text the same way Stash's
    auto-tagger matches names in file paths.

    Lower-cased, separator-insensitive (spaces match '.', '-', '_', space or
    nothing) and bounded by word boundaries. Returns None for an empty name.
    Apply the returned pattern to lower-cased text.
    """
    parts = [re.escape(p) for p in name.lower().split(" ") if p]
    if not parts:
        return None
    core = (_NAME_SEPARATOR + "*").join(parts)
    return re.compile(
        r"(?:^|_|" + _NAME_NOT_WORD + r")" + core + r"(?:$|_|" + _NAME_NOT_WORD + r")",
        re.UNICODE,
    )


class MediaProcessor:
    def __init__(self, max_title_length):
        self.max_title_length = max_title_length

    def remove_html_tags(self, text):
        text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
        text = _TAG_RE.sub("", text)
        return html.unescape(text)

    def truncate_title(self, title, max_length):
        if len(title) <= max_length:
            return title
        punctuation_chars = {".", "!", "?", "❤", "☺"}
        punctuation_chars.update(_EMOJI_RE.findall(title))
        last_punctuation_index = -1
        for c in punctuation_chars:
            last_punctuation_index = max(
                title.rfind(c, 0, max_length), last_punctuation_index
            )
        if last_punctuation_index != -1:
            return title[: last_punctuation_index + 1]
        last_space_index = title.rfind(" ", 0, max_length)
        title_end = last_space_index if last_space_index != -1 else max_length
        return title[:title_end]

    def process_text(self, text):
        """Return (title, details) for a piece of post text."""
        parts = _BR_RE.split(text, maxsplit=1)
        title = self.remove_html_tags(parts[0])
        details = self.remove_html_tags(text)
        if len(title) > self.max_title_length:
            title = self.truncate_title(title, self.max_title_length)
        if title == details:
            details = ""
        return title, details

    def parse_mentions(self, text):
        mentions = []
        for match in _MENTION_RE.findall(text):
            name = match.lower()
            if name not in mentions:
                mentions.append(name)
        return mentions

    def studio_code(self, filename):
        if not filename:
            return ""
        basename = os.path.splitext(filename)[0]
        return basename.removesuffix("_source")

    def format_date(self, posted_at):
        if not posted_at:
            return ""
        try:
            return datetime.fromisoformat(posted_at).strftime("%Y-%m-%d")
        except ValueError:
            # Fall back to the leading YYYY-MM-DD if the timestamp is unusual.
            return str(posted_at)[:10]
