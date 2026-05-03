# SPDX-FileCopyrightText: 2026 Kaspar Winckler
# SPDX-License-Identifier: GPL-3.0-or-later

import html
import re
import requests
import requests_cache
import time
import urllib.parse

import kplugin

MAX_AGE = 300
TIMEOUT = 10

SESSION = requests_cache.CachedSession(
    cache_control=True,
    cache_name=kplugin.HTTP_CACHE_NAME,
    expire_after=MAX_AGE,
)


def get_art(item):
    item = item or {}
    images = item.get("images") or []
    images_by_type = {
        image["type"]: image["urls"].get("w:1024") or list(image["urls"].values())[-1]
        for image in images
        if "type" in image and "urls" in image
    }
    art = {
        "fanart": images_by_type.get("background_16x9"),
        "poster": images_by_type.get("vignette_2x3")
        or images_by_type.get("vignette_3x4")
        or images_by_type.get("carre"),
        "thumb": images_by_type.get("carre") or images_by_type.get("vignette_3x4"),
    }
    return {k: v for k, v in art.items() if v}


REMOVE_TAGS = re.compile("<.*?>")


def remove_html(s):
    if s:
        s = re.sub(REMOVE_TAGS, "", s)
        s = html.unescape(s)
        return s


def strftime(e):
    if e:
        return time.strftime("%Y-%m-%d", time.localtime(e))


def get_title(item):
    program = item.get("program")

    if program:
        title = item.get("title") or ""
        program_label = program.get("label") or ""
        if not title.lower().startswith(program_label.lower()):
            return f"{program_label} : {title}"

    return item.get("label") or item.get("episode_title") or item.get("title")


def get_video_info(item):
    return {
        "duration": item.get("duration"),
        "episode": item.get("episode"),
        "firstaired": strftime(item.get("broadcast_begin_date")),
        "mediatype": "video",
        "mpaa": item.get("rating_csa"),
        "plot": remove_html(item.get("description")),
        "season": item.get("season"),
        "title": get_title(item),
        "year": item.get("production_year"),
    }


class Player(kplugin.Playable, qargs=["si_id"]):
    def __init__(self, item=None, **kwargs):
        super().__init__(**kwargs)
        self.si_id = self.query.get("si_id")
        self.item = item or {}

    def get_list_item(self):
        title = get_title(self.item)

        return {
            "label": title,
            "art": get_art(self.item.get("program")) | get_art(self.item),
            "videoinfotag": get_video_info(self.item),
        }

    def open(self, prefer_hls=False):

        os = "ios" if prefer_hls else "androidtv"

        headers = urllib.parse.urlencode(
            {
                "User-Agent": "Mozilla/5.0 (Linux; Android 10; K)"
                " AppleWebKit/537.36 (KHTML, like Gecko)"
                " Chrome/138.0.7204.46"
                " Mobile Safari/537.36"
            }
        )

        url = (
            "https://player.webservices.francetelevisions.fr/v1/videos/"
            f"{self.si_id}?country_code=FR&os={os}"
        )

        data = SESSION.get(url, timeout=TIMEOUT).json()

        if message := data.get("message"):
            raise Exception(message)

        video = data["video"]
        token_data = requests.get(
            video["token"],
            params={"url": video["url"]},
            timeout=TIMEOUT,
        ).json()

        stream_url = token_data["url"]
        path = f"{stream_url}|{headers}"

        properties = {
            "inputstream": "inputstream.adaptive",
            "inputstream.adaptive.manifest_headers": headers,
            "inputstream.adaptive.stream_headers": headers,
        }

        if ".mpd" in stream_url:
            properties["mimetype"] = "application/dash+xml"
        else:
            properties["mimetype"] = "application/vnd.apple.mpegurl"
            properties["inputstream.adaptive.license_key"] = (
                f"https://simulcast-b.ftven.fr/keys/hls.key|{headers}"
            )

        return {
            "path": path,
            "properties": properties,
        }


class Folder(kplugin.Folder):
    url = ""
    http_params = {"platform": "apps"}
    key = "collections"

    def __init__(self, data=None, **kwargs):
        super().__init__(**kwargs)
        self.index = self.query.get("index")
        self.data = data
        self.loaded = False

    def load(self):
        if self.loaded:
            return

        if not self.data:
            url = self.url.format(**self.get_query())
            self.data = SESSION.get(
                url,
                params=self.http_params,
                timeout=TIMEOUT,
            ).json()

        self.parent_art = {}
        self.item = self.data.get("item") or self.data
        self.items = self.data.get(self.key) or []

        if self.index is not None:
            self.index = int(self.index)
            self.parent_art = get_art(self.item)
            self.item = self.items[self.index] if self.index < len(self.items) else {}
            self.items = self.item.get("items") or []

        self.loaded = True

    def get_list_item(self):
        self.load()
        return {
            "label": self.item.get("label") or self.item.get("title"),
            "art": self.parent_art | get_art(self.item),
        }

    def open(self, on_demand=False):
        self.load()
        live = self.item.get("type") == "live"

        for index, item in enumerate(self.items):
            if live:
                category = item.get("category") or {}
                images = category.get("images")
                item = item.get("channel") or item.get("partner") or item
                item["images"] = item.get("images") or images

            si_id = item.get("si_id")
            item_type = item.get("type") or ""
            if si_id and not (on_demand and item_type in ("channel", "partner")):
                yield Player(
                    si_id=si_id,
                    item=item,
                )

            elif item_type == "categorie":
                yield Categorie(
                    path=item.get("url_complete"),
                    data=item,
                )

            elif item_type in (
                "channel",
                "collection",
                "event",
                "partner",
                "program",
                "sous_categorie",
            ):
                yield Program(
                    path=item.get("url_complete")
                    or item.get("channel_path")
                    or item.get("collection_path")
                    or item.get("partner_path")
                    or item.get("program_path"),
                    data=item,
                )

            elif item_type == "region":
                yield Region(
                    path=item.get("region_path"),
                    data=item,
                )

            elif item_type.startswith("playlist") or item_type in (
                "categories_home",
                "live",
                "mise_en_avant",
                "regions",
            ):
                yield kplugin.resolve(
                    **self.get_query(),
                    index=index,
                    data=self.data,
                )

            elif item_type in ["article", "link"]:
                continue

            else:
                raise Exception(f"Type inconnu : {item_type}")


class Categorie(Folder, qargs=["path", "index"]):
    url = "https://api-mobile.yatta.francetv.fr/apps/categories/{path}"


class Program(Folder, qargs=["path", "index"]):
    url = "https://api-mobile.yatta.francetv.fr/apps/program/{path}"


class Region(Folder, qargs=["path", "territoire", "index"]):
    url = "https://api-mobile.yatta.francetv.fr/apps/regions/{path}/{territoire}"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.query["territoire"] = (
            "metropole"
            if (self.query.get("path") or "").startswith("france")
            else "outre-mer"
        )


class FranceTV(Folder, qargs=["index"]):
    url = "https://api-mobile.yatta.francetv.fr/apps/page/_"


class Directs(Folder, qargs=["index"]):
    url = "https://api-mobile.yatta.francetv.fr/apps/directs/_"

    def __init__(self, index=0, **kwargs):
        super().__init__(index=index, **kwargs)


class Replays(Directs, qargs=["index"]):
    def get_list_item(self):
        return {"label": "Les chaines en replay"}

    def open(self):
        return super().open(on_demand=True)


class Territoire(Folder, qargs=["path", "index"]):
    url = "https://api-mobile.yatta.francetv.fr/apps/regions/{path}"
    key = "items"


class Regions(kplugin.Folder, qargs=["path", "index"]):
    def get_list_item(self):
        return {"label": "Les Régions"}

    def open(self):
        for territoire in ("metropole", "outre-mer"):
            yield from Territoire(path=territoire).open()


class Recherche(kplugin.Search):
    def get_list_item(self):
        return {"label": "Recherche"}

    def open(self):
        yield from Resultat(term=self.query).open()


class Resultat(Folder, qargs=["term", "index"]):
    url = "https://api-mobile.yatta.francetv.fr/apps/search"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.http_params = {
            "filters": "with-collections,with-lives",
            "platform": "apps",
            "term": self.query.get("term"),
        }


class Home(kplugin.Folder):
    def open(self):
        yield FranceTV()
        yield Directs()
        yield Replays()
        yield Regions()
        yield Recherche()


def run():
    kplugin.kodi_run(Home)
    SESSION.cache.delete(expired=True)
