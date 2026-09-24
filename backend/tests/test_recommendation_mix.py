"""Quota allocation, source synthesis, and duplicate handling for fallback picks."""
import random
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import recommendation_mix as mix


def candidates(count=20):
    return [{"id": f"{source}:{index}", "title": f"Video {index}", "origins": [source],
             "user_added": source == "watch_later", "verified": source == "custom_search"}
            for source in mix.DEFAULT_WEIGHTS for index in range(count)]


class RecommendationMixTest(unittest.TestCase):
    def test_default_ten_item_playlist_has_five_five_unique_random_picks(self):
        result = mix.weighted_fallback(candidates(), 10, rng=random.Random(1))
        self.assertEqual(Counter(item["fallback_source"] for item in result),
                         {"custom_search": 5, "watch_later": 5})
        self.assertEqual(len({item["id"] for item in result}), 10)
        alternate = mix.weighted_fallback(candidates(), 10, rng=random.Random(2))
        self.assertNotEqual([item["id"] for item in result], [item["id"] for item in alternate])

    def test_small_playlist_rounds_and_missing_capacity_is_redistributed(self):
        result = mix.weighted_fallback(candidates(), 8, rng=random.Random(1))
        self.assertEqual(Counter(item["fallback_source"] for item in result),
                         {"custom_search": 4, "watch_later": 4})
        pool = [item for item in candidates() if item["origins"] != ["watch_later"]]
        result = mix.weighted_fallback(pool, 10, rng=random.Random(1))
        self.assertEqual(Counter(item["fallback_source"] for item in result), {"custom_search": 10})
        sparse = [*pool, {"id": "diy:one", "origins": ["watch_later"], "user_added": True}]
        result = mix.weighted_fallback(sparse, 10, rng=random.Random(1))
        self.assertEqual(len(result), 10)
        self.assertEqual(sum(item["fallback_source"] == "watch_later" for item in result), 1)

    def test_zero_weight_sources_never_fill_missing_slots(self):
        weights = {"custom_search": 0, "public_search": 0, "watch_later": 100}
        result = mix.weighted_fallback(candidates(2), 10, weights, random.Random(1))
        self.assertEqual(len(result), 2)
        self.assertTrue(all(item["fallback_source"] == "watch_later" for item in result))
        self.assertEqual(mix.weighted_fallback(candidates(0), 8, weights), [])

    def test_one_video_with_multiple_origins_is_only_selected_once(self):
        item = {"id": "vimeo.com:123", "origins": list(mix.DEFAULT_WEIGHTS), "user_added": True, "verified": False}
        result = mix.weighted_fallback([item, item], 10, rng=random.Random(1))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["fallback_source"], "watch_later")
        self.assertFalse(result[0]["verified"])

    def test_merge_preserves_curation_origins_and_verified_metadata(self):
        saved = {"id": "one", "title": "My title", "description": "My note", "user_added": True,
                 "verified": False, "verification": "watch_later", "origins": ["watch_later"]}
        discovered = {"id": "one", "title": "Provider title", "verified": True, "user_added": False,
                      "verification": "provider_search", "duration": 60, "origins": ["custom_search"]}
        for first, second in ((saved, discovered), (discovered, saved)):
            merged = mix.merge(first, second)
            self.assertEqual(merged["title"], "My title")
            self.assertEqual(merged["description"], "My note")
            self.assertEqual(set(merged["origins"]), {"watch_later", "custom_search"})
            self.assertTrue(merged["verified"])
            self.assertTrue(merged["user_added"])
            self.assertEqual(merged["verification"], "provider_search")
            self.assertEqual(merged["duration"], 60)

    def test_rank_context_representative_across_two_sources(self):
        result = mix.ranking_candidates(candidates(100), limit=48)
        self.assertEqual(len(result), 48)
        self.assertEqual(Counter(item["origins"][0] for item in result),
                         {"custom_search": 24, "watch_later": 24})

    def test_url_only_saved_entry_does_not_replace_new_provider_title(self):
        saved = {"id": "one", "title": "https://vimeo.com/123", "user_added": True,
                 "user_title": None, "user_description": None, "description": "Old inherited text",
                 "origins": ["watch_later"], "verified": False}
        live = {"id": "one", "title": "Fresh title", "description": "Fresh description", "verified": True,
                "verification": "provider_search", "origins": ["custom_search"]}
        for first, second in ((saved, live), (live, saved)):
            merged = mix.merge(first, second)
            self.assertEqual(merged["title"], "Fresh title")
            self.assertEqual(merged["description"], "Fresh description")
            self.assertTrue(merged["user_added"])


if __name__ == "__main__":
    unittest.main()
