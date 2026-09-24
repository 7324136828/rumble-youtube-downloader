"""Merge discovered metadata and choose a randomized, weighted fallback playlist."""
import random

DEFAULT_WEIGHTS = {"custom_search": 50, "watch_later": 50}
SOURCE_LABELS = {"custom_search": "website search", "watch_later": "Watch later"}


def origins(item):
    values = item.get("origins")
    result = {value for value in values if value in DEFAULT_WEIGHTS} if isinstance(values, list) else set()
    if item.get("user_added") is True:
        result.add("watch_later")
    if not result:
        verification = item.get("verification")
        if verification == "custom_search" or item.get("verified") is True:
            result.add("custom_search")
    return sorted(result)


def merge(previous, item):
    """Prefer confirmed metadata while retaining explicit user curation."""
    if previous is None:
        return {**item, "origins": origins(item), "user_added": item.get("user_added") is True}
    primary, secondary = (item, previous) if item.get("verified") is True and previous.get("verified") is not True else (previous, item)
    result = {**secondary, **{key: value for key, value in primary.items() if value is not None}}
    result["origins"] = sorted(set(origins(previous)) | set(origins(item)))
    result["user_added"] = previous.get("user_added") is True or item.get("user_added") is True
    result["verified"] = previous.get("verified") is True or item.get("verified") is True
    curated = item if item.get("user_added") is True else previous if previous.get("user_added") is True else None
    if curated:
        for field in ("title", "description"):
            value = curated.get("user_" + field) if "user_" + field in curated else curated.get(field)
            if value:
                result[field] = value
        if curated.get("catalog_id") is not None:
            result["catalog_id"] = curated["catalog_id"]
    return result


def weighted_fallback(candidates, limit, weights=None, rng=None):
    """Round quotas, randomize within sources, and redistribute missing capacity.

    A video counts once. Explicit saves belong to Watch later; otherwise a
    website discovery takes precedence over the same URL in a public index.
    Zero-weight sources never participate, even when other sources run out.
    """
    weights = weights or DEFAULT_WEIGHTS
    rng = rng or random.SystemRandom()
    pools = {source: [] for source in DEFAULT_WEIGHTS}
    seen = set()
    for item in candidates:
        if item["id"] in seen:
            continue
        available = set(origins(item))
        source = next((source for source in ("watch_later", "custom_search")
                       if source in available and weights.get(source, 0) > 0), None)
        if source:
            seen.add(item["id"])
            pools[source].append(item)
    for pool in pools.values():
        rng.shuffle(pool)
    active = [source for source, pool in pools.items() if pool and weights.get(source, 0) > 0]
    if not active or limit <= 0:
        return []
    total = sum(weights[source] for source in active)
    targets = {source: limit * weights[source] / total for source in active}
    quotas = {source: int(targets[source]) for source in active}
    # Random tie breaking avoids consistently rounding away a small source.
    remainder_order = active[:]
    rng.shuffle(remainder_order)
    remainder_order.sort(key=lambda source: targets[source] - quotas[source], reverse=True)
    for source in remainder_order[:limit - sum(quotas.values())]:
        quotas[source] += 1
    selected = []
    def take(source):
        item = pools[source].pop()
        selected.append({**item, "fallback_source": source,
                         "reason": f"Random pick from {SOURCE_LABELS[source]}"})
    for source in active:
        for _ in range(min(quotas[source], len(pools[source]))):
            take(source)
    while len(selected) < limit:
        active = [source for source in active if pools[source]]
        if not active:
            break
        source = rng.choices(active, weights=[weights[source] for source in active], k=1)[0]
        take(source)
    rng.shuffle(selected)
    return selected


def ranking_candidates(candidates, limit=48):
    """Give all discovery sources representation in the bounded model context."""
    pools = {source: [] for source in DEFAULT_WEIGHTS}
    extra = []
    for item in candidates:
        source = next((source for source in ("watch_later", "custom_search") if source in origins(item)), None)
        (pools[source] if source else extra).append(item)
    selected, seen = [], set()
    for index in range(max((len(pool) for pool in [*pools.values(), extra]), default=0)):
        for pool in [*pools.values(), extra]:
            if index < len(pool) and pool[index]["id"] not in seen:
                seen.add(pool[index]["id"])
                selected.append(pool[index])
                if len(selected) >= limit:
                    return selected
    return selected
