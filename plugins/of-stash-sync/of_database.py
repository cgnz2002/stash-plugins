"""Reader for OF-Scraper's user_data.db sqlite databases.

Schema verified against OF-Scraper 3.14.7 (sample database). Databases are
opened read-only so a running OF-Scraper or a locked file never causes a write
or "readonly database" error.
"""

import glob
import os
import sqlite3


class OFDatabase:
    # Post text can live in any of these tables, all sharing the same columns.
    # 'others' and 'products' are present in OF-Scraper 3.14.7 and were missed
    # by the original sync tool.
    TEXT_TABLES = ["posts", "stories", "messages", "others", "products"]

    def __init__(self, path):
        self.path = path
        uri = "file:{}?mode=ro".format(os.path.abspath(path))
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row

    @staticmethod
    def find_databases(data_path):
        search = os.path.join(data_path, "**", "user_data.db")
        return sorted(glob.glob(search, recursive=True))

    def profiles(self):
        return self.conn.execute(
            "SELECT user_id, username FROM profiles"
        ).fetchall()

    def media_by_filename(self, user_id, filename):
        return self.conn.execute(
            "SELECT media_id, post_id, link, filename, api_type, media_type, posted_at "
            "FROM medias WHERE model_id = ? AND filename = ?",
            (user_id, filename),
        ).fetchone()

    def post_meta(self, post_id):
        """Return the first matching post/story/message/other/product row, if any."""
        for table in self.TEXT_TABLES:
            try:
                row = self.conn.execute(
                    "SELECT text, price, paid, archived FROM {} WHERE post_id = ?".format(
                        table
                    ),
                    (post_id,),
                ).fetchone()
            except sqlite3.Error:
                # Table may not exist in older databases; skip it.
                continue
            if row:
                return row
        return None

    def close(self):
        self.conn.close()
