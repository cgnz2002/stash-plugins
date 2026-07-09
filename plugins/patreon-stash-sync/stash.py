"""Minimal, dependency-free Stash GraphQL client.

Uses only the Python standard library (urllib + json). Authenticates with the
session cookie that Stash passes to every plugin task on stdin, so no API key is
required. All queries/mutations and their fields were verified against the
Stash v0.31.1 GraphQL schema.
"""

import json
import urllib.request
import urllib.error

import log


def _summarize(update):
    """Compact one-line summary of an update input for dry-run logging."""
    parts = []
    for key in ("title", "date", "photographer"):
        if update.get(key):
            parts.append("{}={!r}".format(key, update[key]))
    if update.get("urls"):
        parts.append("url={}".format(update["urls"][0]))
    if "studio_id" in update:
        parts.append("studio={}".format(update["studio_id"]))
    if "performer_ids" in update:
        parts.append("performers={}".format(len(update["performer_ids"])))
    if "tag_ids" in update:
        parts.append("tags={}".format(len(update["tag_ids"])))
    if update.get("details"):
        parts.append("details[{}]".format(len(update["details"])))
    return ", ".join(parts) or "(no fields)"


class StashClient:
    def __init__(self, server_connection, dry_run=False):
        scheme = server_connection.get("Scheme") or "http"
        port = server_connection.get("Port") or 9999
        cookie = server_connection.get("SessionCookie") or {}
        self.session = cookie.get("Value") or ""
        # The plugin always runs on the same host as the Stash server.
        self.url = "{}://localhost:{}/graphql".format(scheme, port)
        # When set, every mutating call is logged and skipped (reads still run),
        # so a task can preview exactly what a real sync would change.
        self.dry_run = dry_run

    def call(self, query, variables=None):
        payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        req = urllib.request.Request(self.url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        if self.session:
            req.add_header("Cookie", "session={}".format(self.session))
        try:
            # Generous timeout: the two bulk gallery/image fetches can scan a
            # large library once each.
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            raise RuntimeError("GraphQL HTTP {}: {}".format(e.code, detail))
        except urllib.error.URLError as e:
            raise RuntimeError("GraphQL connection error: {}".format(e.reason))
        if body.get("errors"):
            raise RuntimeError("GraphQL error: {}".format(body["errors"]))
        return body.get("data") or {}

    # ----- configuration -------------------------------------------------

    def get_plugin_config(self, plugin_id):
        data = self.call("query { configuration { plugins } }")
        plugins = (data.get("configuration") or {}).get("plugins") or {}
        return plugins.get(plugin_id) or {}

    # ----- studios -------------------------------------------------------

    def find_studio(self, name):
        query = """
        query FindStudios($f: StudioFilterType!) {
            findStudios(studio_filter: $f, filter: { per_page: -1 }) {
                studios { id name }
            }
        }
        """
        variables = {"f": {"name": {"value": name, "modifier": "EQUALS"}}}
        studios = self.call(query, variables)["findStudios"]["studios"]
        for studio in studios:
            if studio["name"].lower() == name.lower():
                return studio["id"]
        return None

    def create_studio(self, name, parent_id, url, details, image=None):
        query = """
        mutation StudioCreate($input: StudioCreateInput!) {
            studioCreate(input: $input) { id name }
        }
        """
        if self.dry_run:
            log.LogInfo("[dry-run] would create studio '{}'".format(name))
            return "dry:studio:{}".format(name)
        studio_input = {
            "name": name,
            "urls": [url],
            "details": details,
            "ignore_auto_tag": False,
        }
        if parent_id:
            studio_input["parent_id"] = parent_id
        if image:
            studio_input["image"] = image
        return self.call(query, {"input": studio_input})["studioCreate"]["id"]

    # ----- performers ----------------------------------------------------

    def find_performers_by_name(self, username):
        """Look up performers matching an OF username.

        Returns a dict with:
          'exact'     - performers whose name or an alias equals the username
                        (case-insensitive). These are the ones we attach.
          'name_like' - raw results of the name EQUALS query. Stash compiles
                        EQUALS to SQL LIKE (where '_' is a single-character
                        wildcard) and uses that same query to block duplicate
                        names on create. Keeping these lets an underscore
                        username such as 'ace_carter' be matched to an existing
                        performer ('ace carter') instead of failing to create a
                        duplicate.
        """
        query = """
        query FindPerformers($f: PerformerFilterType!) {
            findPerformers(performer_filter: $f, filter: { per_page: -1 }) {
                performers { id name alias_list tags { id } }
            }
        }
        """
        name_like = self.call(
            query, {"f": {"name": {"value": username, "modifier": "EQUALS"}}}
        )["findPerformers"]["performers"]
        by_alias = self.call(
            query, {"f": {"aliases": {"value": username, "modifier": "INCLUDES"}}}
        )["findPerformers"]["performers"]

        exact = []
        seen = set()
        for performer in name_like + by_alias:
            if performer["id"] in seen:
                continue
            names = [performer["name"]] + (performer.get("alias_list") or [])
            if any(n.lower() == username.lower() for n in names):
                exact.append(performer)
                seen.add(performer["id"])
        return {"exact": exact, "name_like": name_like}

    def create_performer(self, name, url):
        query = """
        mutation PerformerCreate($input: PerformerCreateInput!) {
            performerCreate(input: $input) { id name }
        }
        """
        if self.dry_run:
            log.LogInfo("[dry-run] would create performer '{}'".format(name))
            return "dry:performer:{}".format(name)
        try:
            return self.call(query, {"input": {"name": name, "urls": [url]}})[
                "performerCreate"
            ]["id"]
        except RuntimeError as e:
            log.LogWarning("Could not create performer '{}': {}".format(name, e))
            return None

    # ----- tags ----------------------------------------------------------

    def find_tag(self, name):
        query = """
        query FindTags($f: TagFilterType!) {
            findTags(tag_filter: $f, filter: { per_page: -1 }) {
                tags { id name }
            }
        }
        """
        tags = self.call(query, {"f": {"name": {"value": name, "modifier": "EQUALS"}}})[
            "findTags"
        ]["tags"]
        for tag in tags:
            if tag["name"].lower() == name.lower():
                return tag["id"]
        return None

    def find_all_tags(self):
        query = """
        query AllTags {
            findTags(filter: { per_page: -1 }) {
                tags { id name aliases ignore_auto_tag }
            }
        }
        """
        return self.call(query)["findTags"]["tags"]

    def create_tag(self, name):
        query = """
        mutation TagCreate($input: TagCreateInput!) {
            tagCreate(input: $input) { id name }
        }
        """
        if self.dry_run:
            log.LogInfo("[dry-run] would create tag '{}'".format(name))
            return "dry:tag:{}".format(name)
        try:
            return self.call(query, {"input": {"name": name}})["tagCreate"]["id"]
        except RuntimeError as e:
            log.LogError("Could not create tag '{}': {}".format(name, e))
            return None

    # ----- galleries -----------------------------------------------------
    #
    # A patreon-dl post folder is ingested by Stash as a folder-based gallery,
    # so posts sync onto galleries (and the images inside them). Gallery update
    # inputs carry the same metadata fields as scenes/images except that only
    # ``photographer`` applies (galleries have no ``director``). Verified against
    # the Stash v0.31.x schema.

    def find_galleries(self, path, include_organized):
        query = """
        query FindGalleries($f: GalleryFilterType!) {
            findGalleries(gallery_filter: $f, filter: { per_page: -1 }) {
                galleries {
                    id title details date urls organized photographer
                    tags { id }
                    performers { id }
                    studio { id }
                    folder { path }
                    files { path }
                }
            }
        }
        """
        gallery_filter = {"path": {"value": path, "modifier": "INCLUDES"}}
        if not include_organized:
            gallery_filter["organized"] = False
        return self.call(query, {"f": gallery_filter})["findGalleries"]["galleries"]

    def find_gallery_by_title(self, title):
        """Find a gallery by exact title (case-insensitive), for idempotent
        collection-gallery creation. Returns the first matching id or None."""
        query = """
        query FindGalleries($f: GalleryFilterType!) {
            findGalleries(gallery_filter: $f, filter: { per_page: -1 }) {
                galleries { id title }
            }
        }
        """
        variables = {"f": {"title": {"value": title, "modifier": "EQUALS"}}}
        galleries = self.call(query, variables)["findGalleries"]["galleries"]
        for gallery in galleries:
            if (gallery.get("title") or "").lower() == title.lower():
                return gallery["id"]
        return None

    def create_gallery(self, title, urls, details, date, studio_id, performer_ids, tag_ids):
        query = """
        mutation GalleryCreate($input: GalleryCreateInput!) {
            galleryCreate(input: $input) { id title }
        }
        """
        gallery_input = {"title": title, "organized": True}
        if urls:
            gallery_input["urls"] = urls
        if details:
            gallery_input["details"] = details
        if date:
            gallery_input["date"] = date
        if studio_id:
            gallery_input["studio_id"] = studio_id
        if performer_ids:
            gallery_input["performer_ids"] = performer_ids
        if tag_ids:
            gallery_input["tag_ids"] = tag_ids
        if self.dry_run:
            log.LogInfo("[dry-run] would create gallery: {}".format(_summarize(gallery_input)))
            return "dry:gallery:{}".format(title)
        try:
            return self.call(query, {"input": gallery_input})["galleryCreate"]["id"]
        except RuntimeError as e:
            log.LogError("Could not create gallery '{}': {}".format(title, e))
            return None

    def update_gallery(self, gallery_input):
        query = """
        mutation GalleryUpdate($input: GalleryUpdateInput!) {
            galleryUpdate(input: $input) { id }
        }
        """
        if self.dry_run:
            log.LogInfo("[dry-run] gallery {}: {}".format(
                gallery_input.get("id"), _summarize(gallery_input)))
            return
        self.call(query, {"input": gallery_input})

    def add_gallery_images(self, gallery_id, image_ids):
        """Attach images to a gallery (images may belong to several galleries).
        Used to pull a collection's member-post images into its gallery."""
        if not image_ids:
            return
        if self.dry_run:
            log.LogInfo("[dry-run] would attach {} image(s) to gallery {}".format(
                len(image_ids), gallery_id))
            return
        query = """
        mutation AddGalleryImages($id: ID!, $ids: [ID!]!) {
            addGalleryImages(input: { gallery_id: $id, image_ids: $ids })
        }
        """
        self.call(query, {"id": gallery_id, "ids": image_ids})

    # ----- images --------------------------------------------------------

    def find_images(self, path, include_organized):
        query = """
        query FindImages($f: ImageFilterType!) {
            findImages(image_filter: $f, filter: { per_page: -1 }) {
                images {
                    id
                    organized
                    tags { id }
                    performers { id }
                    photographer
                    visual_files {
                        ... on VideoFile { path basename }
                        ... on ImageFile { path basename }
                    }
                }
            }
        }
        """
        image_filter = {"path": {"value": path, "modifier": "INCLUDES"}}
        if not include_organized:
            image_filter["organized"] = False
        return self.call(query, {"f": image_filter})["findImages"]["images"]

    def update_image(self, image_input):
        query = """
        mutation ImageUpdate($input: ImageUpdateInput!) {
            imageUpdate(input: $input) { id }
        }
        """
        if self.dry_run:
            log.LogInfo("[dry-run] image {}: {}".format(
                image_input.get("id"), _summarize(image_input)))
            return
        self.call(query, {"input": image_input})
