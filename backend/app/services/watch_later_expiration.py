"""Clean up expired saved-video memberships and their temporary artwork."""
from . import db, watch_later_thumbnails


def purge_expired(now=None):
    with db.RETENTION_POLICY_LOCK:
        expired = db.expire_watch_later(now)
        for entry in expired:
            watch_later_thumbnails.remove_path(entry["thumbnail_path"])
    return [entry["catalog_id"] for entry in expired]
