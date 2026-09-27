"""Explicit saves use public URLs without recommendation provider authorization."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.manual_video_urls import normalize_video_url
from app.services.recommendation_tools import canonical_video_url


class ManualVideoUrlTest(unittest.TestCase):
    def test_native_videos_work_without_recommendation_providers(self):
        self.assertEqual(normalize_video_url('https://youtu.be/abcdefghijk?t=12', [])[1],
                         'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(normalize_video_url('https://rumble.com/v123abc-title.html', [])[0], 'rumble')

    def test_direct_media_and_unknown_sites_preserve_functional_urls(self):
        url = 'https://archive.org/download/movie/movie.mp4?token=abc#player'
        source, saved, ident = normalize_video_url(url, [])
        self.assertEqual(source, 'archive.org')
        self.assertEqual(saved, url.split('#')[0])
        self.assertTrue(ident.startswith('archive.org:'))
        self.assertIsNone(canonical_video_url(url, []))

    def test_direct_urls_do_not_inherit_recommendation_domain_or_port_rules(self):
        for url in ('https://example.com/video.mp4', 'https://archive.org:8443/video.mp4',
                    'https://8.8.8.8/video.mp4'):
            with self.subTest(url=url):
                self.assertEqual(normalize_video_url(url, [])[1], url)

    def test_invalid_urls_still_cannot_be_saved(self):
        for url in ('javascript:alert(1)', 'file:///movie.mp4', 'https://localhost/video',
                    'https://user:secret@archive.org/movie', 'https://archive.org/vi deo',
                    'https://archive.org/%0avideo', 'https://youtube.com/channel/abc',
                    'https://127.0.0.1/video.mp4', 'https://10.0.0.1/video.mp4'):
            with self.subTest(url=url):
                self.assertIsNone(normalize_video_url(url, []))
