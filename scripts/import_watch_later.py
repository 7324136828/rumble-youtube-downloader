#!/usr/bin/env python3
"""Import a JSON video list into the app's watch-later catalog using only stdlib."""
import argparse
import json
import sys
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_VIDEOS = 10000
BATCH_SIZE = 200
REQUEST_TIMEOUT = 20
FIELD_LIMITS = {"source_url": 2048, "title": 500, "description": 10000}


class ImportErrorDetail(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ImportErrorDetail("Duplicate JSON object keys are not allowed.")
        value[key] = item
    return value


def validate_payload(payload):
    if isinstance(payload, dict):
        if set(payload) != {"videos"}:
            raise ImportErrorDetail("The JSON object must contain only a videos array.")
        payload = payload["videos"]
    if not isinstance(payload, list) or not 1 <= len(payload) <= MAX_VIDEOS:
        raise ImportErrorDetail(f"Provide an array containing 1 to {MAX_VIDEOS:,} videos.")
    cleaned = []
    for index, item in enumerate(payload, 1):
        if not isinstance(item, dict) or "source_url" not in item or set(item) - FIELD_LIMITS.keys():
            raise ImportErrorDetail(f"Video {index} must contain source_url and optionally title and description only.")
        video = {}
        for field, value in item.items():
            if value is None and field != "source_url":
                video[field] = None
                continue
            if not isinstance(value, str) or len(value) > FIELD_LIMITS[field]:
                raise ImportErrorDetail(f"Video {index}: {field} must be a string up to {FIELD_LIMITS[field]:,} characters.")
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise ImportErrorDetail(f"Video {index}: {field} must contain valid Unicode text.") from exc
            value = value.strip()
            if field == "source_url" and not value:
                raise ImportErrorDetail(f"Video {index}: source_url cannot be blank.")
            video[field] = value
        cleaned.append(video)
    return cleaned


def load_input(path, stdin):
    try:
        if path == "-":
            data = getattr(stdin, "buffer", stdin).read(MAX_INPUT_BYTES + 1)
        else:
            with open(path, "rb") as stream:
                data = stream.read(MAX_INPUT_BYTES + 1)
        if isinstance(data, str):
            data = data.encode("utf-8")
        if len(data) > MAX_INPUT_BYTES:
            raise ImportErrorDetail("The JSON input exceeds 16 MiB.")
        payload = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ImportErrorDetail("Could not read a valid UTF-8 JSON input file.") from exc
    return validate_payload(payload)


def _endpoint(api):
    try:
        parsed = urlsplit(api)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "#" in api or "\\" in api
                or any(character.isspace() or ord(character) < 32 for character in api)):
            raise ValueError()
        parsed.port
    except ValueError as exc:
        raise ImportErrorDetail("--api must be an HTTP(S) base URL without credentials, query, or fragment.") from exc
    return api.rstrip("/") + "/api/recommendations/watch-later"


def _plain(value):
    if not isinstance(value, str):
        return ""
    value = value.encode("utf-8", errors="replace").decode("utf-8")
    return " ".join("".join(character for character in value if ord(character) >= 32 and ord(character) != 127).split())[:500]


def _error_detail(body):
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        return ""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str):
        return _plain(detail)
    if isinstance(detail, list):
        # FastAPI validation errors include the original input: only print msgs.
        return "; ".join(filter(None, (_plain(item.get("msg")) for item in detail[:3] if isinstance(item, dict))))[:500]
    return ""


def post_batch(opener, endpoint, videos, *, fetch_titles=True, resolve_redirects=True):
    payload = {"videos": videos}
    if not fetch_titles:
        payload["fetch_titles"] = False
    if not resolve_redirects:
        payload["resolve_redirects"] = False
    request = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        try:
            try:
                detail = _error_detail(exc.read(65537)[:65536])
            except (OSError, HTTPException):
                detail = ""
        finally:
            exc.close()
        raise ImportErrorDetail(f"HTTP {exc.code}" + (f": {detail}" if detail else ".")) from exc
    except (URLError, OSError, HTTPException, UnicodeError) as exc:
        raise ImportErrorDetail("The API could not be reached or did not respond in time.") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ImportErrorDetail("The API response exceeded the size limit.")
    try:
        result = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ImportErrorDetail("The API returned an unreadable response.") from exc
    if (not isinstance(result, dict) or any(type(result.get(field)) is not int or result[field] < 0 for field in ("added", "updated"))
            or result["added"] + result["updated"] > len(videos)):
        raise ImportErrorDetail("The API did not confirm valid import counts.")
    return result["added"], result["updated"]


def main(argv=None, *, stdin=None, stdout=None, stderr=None):
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    parser = argparse.ArgumentParser(description="Import JSON video links into watch later. No downloads or AI model calls.",
                                     epilog="Input: an array or {\"videos\": [...]} containing source_url and optional title/description. Maximum 10,000 videos / 16 MiB. Website and video URL validation happens in the app.")
    parser.add_argument("input", help="UTF-8 JSON file, or - to read standard input")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="app base URL (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="validate the JSON and print a summary without making requests")
    parser.add_argument("--no-fetch-titles", action="store_true", help="save metadata without background title lookups")
    parser.add_argument("--no-resolve-redirects", action="store_true", help="save canonical input URLs without checking HTTP redirects")
    args = parser.parse_args(argv)
    try:
        videos = load_input(args.input, stdin)
        endpoint = _endpoint(args.api)
    except ImportErrorDetail as exc:
        print(f"Import failed: {exc}", file=stderr)
        return 1
    batches = (len(videos) + BATCH_SIZE - 1) // BATCH_SIZE
    if args.dry_run:
        print(f"Valid JSON: {len(videos)} videos in {batches} batch(es). No requests made; website URLs still require app validation.", file=stdout)
        return 0
    opener = build_opener(_NoRedirect())
    added = updated = confirmed = 0
    for offset in range(0, len(videos), BATCH_SIZE):
        batch = videos[offset:offset + BATCH_SIZE]
        try:
            new, changed = post_batch(opener, endpoint, batch, fetch_titles=not args.no_fetch_titles,
                                      resolve_redirects=not args.no_resolve_redirects)
        except ImportErrorDetail as exc:
            print(f"Import failed at batch {offset // BATCH_SIZE + 1}/{batches}: {exc} "
                  f"Already confirmed: {added} added, {updated} updated ({confirmed} input records). "
                  "Retrying is safe; the app deduplicates URLs.", file=stderr)
            return 1
        added += new
        updated += changed
        confirmed += len(batch)
    print(f"Imported {len(videos)} input records: {added} added, {updated} updated.", file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
