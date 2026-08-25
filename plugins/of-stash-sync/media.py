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

# Profile links to a collaborator, e.g. onlyfans.com/ChicagoNerd (with or
# without scheme/www/trailing punctuation). Some creators credit a collaborator
# with a bare profile URL instead of an @mention, so these are treated as
# mentions too. The captured first path segment is the username. Post URLs have
# the form onlyfans.com/<postid>/<username> (a numeric first segment), so purely
# numeric captures are filtered out in parse_mentions to avoid matching a post id.
_PROFILE_URL_RE = re.compile(
    r"onlyfans\.com/([A-Za-z0-9_\.\-]+)", re.IGNORECASE
)

_TAG_RE = re.compile(r"<[^>]+>")
# Block-level boundaries that should become line breaks so text either side of
# them (e.g. a headline paragraph and a body paragraph) is not glued together.
_BLOCK_BREAK_RE = re.compile(r"<br\s*/?>|</p\s*>|</div\s*>|</h[1-6]\s*>", flags=re.IGNORECASE)
_MULTI_NEWLINE_RE = re.compile(r"\n{2,}")

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
        # Turn block boundaries (br, closing p/div/heading) into newlines first
        # so words on either side are not concatenated, then strip the remaining
        # (inline) tags and unescape entities.
        text = _BLOCK_BREAK_RE.sub("\n", text)
        text = _TAG_RE.sub("", text)
        text = html.unescape(text)
        return _MULTI_NEWLINE_RE.sub("\n", text).strip()

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
        """Return (title, details) for a piece of post text.

        The title is the first line (a headline paragraph, or the text before the
        first break); details is the full cleaned text. remove_html_tags
        normalises block boundaries to newlines so a headline and body are not
        concatenated.
        """
        cleaned = self.remove_html_tags(text)
        title = cleaned.split("\n", 1)[0].strip()
        details = cleaned
        if len(title) > self.max_title_length:
            title = self.truncate_title(title, self.max_title_length)
        if title == details:
            details = ""
        return title, details

    def parse_mentions(self, text):
        """Collaborators credited in the post text.

        Picks up both `@mentions` and bare profile links
        (`onlyfans.com/<username>`), since some creators link a collaborator by
        URL instead of an @mention. A profile URL's username is its first path
        segment; a post URL (`onlyfans.com/<postid>/<username>`) has a numeric
        first segment, so purely-numeric captures are skipped to avoid mistaking
        a post id for a username.
        """
        mentions = []
        for match in _MENTION_RE.findall(text):
            name = match.lower()
            if name not in mentions:
                mentions.append(name)
        for match in _PROFILE_URL_RE.findall(text):
            # Trailing '.'/'-' are almost always sentence punctuation, not part
            # of the username (usernames don't end in a separator).
            name = match.lower().rstrip(".-")
            # Skip the post-id form onlyfans.com/<postid>/<username>.
            if not name or name.isdigit():
                continue
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
