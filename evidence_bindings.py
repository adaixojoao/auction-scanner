"""Version-bound keys, a bounded per-worker cache and full-gallery
fingerprints (Beta)."""
from collections import OrderedDict

from collection_contracts import content_hash


def assessment_key(position, dataset_manifest, semantic_version):
    return content_hash({"position": position, "datasets": dataset_manifest,
                         "semantic_version": semantic_version})


class BoundedCache:
    """Single-owner cache: do not share across threads without external locking.
    Return values must be treated as immutable by the caller. It does not close
    resources (e.g. raster handles) it evicts.
    """
    def __init__(self, limit=256):
        if type(limit) is not int or limit < 1:
            raise ValueError("Positive cache limit required")
        self.limit = limit
        self.items = OrderedDict()

    def get_or_compute(self, key, compute):
        if key in self.items:
            self.items.move_to_end(key)
            return self.items[key]
        value = compute()
        self.items[key] = value
        while len(self.items) > self.limit:
            self.items.popitem(last=False)
        return value

    def clear(self):
        self.items.clear()


def gallery_bindings(validated_urls, selected_count):
    if type(selected_count) is not int or selected_count < 0:
        raise ValueError("Invalid image limit")
    if not isinstance(validated_urls, list) or any(not isinstance(u, str) for u in validated_urls):
        raise ValueError("Validated URL list required")
    unique = list(dict.fromkeys(validated_urls))
    return {"gallery_membership": content_hash(sorted(unique)),
            "gallery_order": content_hash(unique),
            "selected_inputs": content_hash(unique[:selected_count])}


def photo_attribution(payload):
    if not isinstance(payload, dict):
        return "unknown"
    value = payload.get("shows_house")
    return "confirmed_by_model" if value is True else "negative" if value is False else "unknown"


def archive_directory(listing_id):
    if not isinstance(listing_id, str) or not listing_id:
        raise ValueError("Listing identity required")
    return content_hash({"listing_id": listing_id})
