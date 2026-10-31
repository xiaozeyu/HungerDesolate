"""Resolve bounded Steam Workshop metadata for explicit published file IDs."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MAX_IDS = 100
MAX_RESPONSE = 2 * 1024 * 1024
WORKSHOP_ID = re.compile(r"^[0-9]{1,20}$")
API = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
PROFILE = "https://steamcommunity.com/profiles/{steam_id}/?xml=1"
PERSONA = re.compile(r"<steamID>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</steamID>", re.S)


class WorkshopProbeError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def reject(code):
    raise WorkshopProbeError(code)


def normalize_ids(values):
    if not isinstance(values, list) or not values or len(values) > MAX_IDS:
        reject("WORKSHOP_INPUT_INVALID")
    result = []
    for value in values:
        value = str(value)
        if not WORKSHOP_ID.fullmatch(value):
            reject("WORKSHOP_INPUT_INVALID")
        if value not in result:
            result.append(value)
    return result


def clean_text(value, limit):
    if not isinstance(value, str):
        return None
    value = "".join(char for char in value if char in "\n\t" or ord(char) >= 32)
    return value[:limit]


def creator_id(item):
    value = item.get("creator", item.get("creator_steamid"))
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{17}", value):
        return None
    return value


def read_author(steam_id):
    request = urllib.request.Request(PROFILE.format(steam_id=steam_id),
        headers={"User-Agent": "hunger-desolate/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read(262145)
    except Exception:
        return None
    if len(raw) > 262144:
        return None
    try:
        match = PERSONA.search(raw.decode("utf-8", "replace"))
    except (UnicodeError, re.error):
        return None
    return clean_text(match.group(1), 128) if match else None


def query(ids):
    fields = [("itemcount", str(len(ids)))]
    fields.extend((f"publishedfileids[{index}]", value) for index, value in enumerate(ids))
    request = urllib.request.Request(API, data=urllib.parse.urlencode(fields).encode("ascii"), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except Exception:
        reject("WORKSHOP_METADATA_UNAVAILABLE")
    if len(raw) > MAX_RESPONSE:
        reject("WORKSHOP_RESPONSE_TOO_LARGE")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        reject("WORKSHOP_RESPONSE_INVALID")
    details = envelope.get("response", {}).get("publishedfiledetails") if isinstance(envelope, dict) else None
    if not isinstance(details, list) or len(details) > MAX_IDS:
        reject("WORKSHOP_RESPONSE_INVALID")
    items = []
    for item in details:
        if not isinstance(item, dict) or not WORKSHOP_ID.fullmatch(str(item.get("publishedfileid", ""))):
            reject("WORKSHOP_RESPONSE_INVALID")
        creator = creator_id(item)
        items.append({
            "workshopId": str(item["publishedfileid"]),
            "result": item.get("result") if isinstance(item.get("result"), int) else None,
            "title": clean_text(item.get("title"), 256),
            "description": clean_text(item.get("short_description"), 1024),
            "creatorSteamId": creator,
            "author": None,
            "creatorAppId": item.get("creator_appid") if isinstance(item.get("creator_appid"), int) else None,
            "consumerAppId": item.get("consumer_appid") if isinstance(item.get("consumer_appid"), int) else None,
            "timeCreated": item.get("time_created") if isinstance(item.get("time_created"), int) else None,
            "timeUpdated": item.get("time_updated") if isinstance(item.get("time_updated"), int) else None,
            "fileSize": item.get("file_size") if isinstance(item.get("file_size"), int) else None,
            "previewUrl": clean_text(item.get("preview_url"), 2048),
            "contentHandle": clean_text(item.get("hcontent_file"), 256),
        })
    creators = {item["creatorSteamId"]: None for item in items if item["creatorSteamId"]}
    if creators:
        with ThreadPoolExecutor(max_workers=8) as executor:
            authors = dict(zip(creators, executor.map(read_author, creators)))
        for item in items:
            item["author"] = authors.get(item["creatorSteamId"])
    return items


def main(request):
    if not isinstance(request, dict) or request.get("schemaVersion") != 1:
        reject("WORKSHOP_INPUT_INVALID")
    ids = normalize_ids(request.get("workshopIds"))
    print(json.dumps({"schemaVersion": 1, "ok": True, "items": query(ids)}, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main(json.loads(input()))
    except WorkshopProbeError as error:
        print(json.dumps({"schemaVersion": 1, "ok": False, "code": error.code}, separators=(",", ":")))
        raise SystemExit(2)
