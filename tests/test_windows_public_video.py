"""Cross-platform offline tests of the restricted Windows public video path."""
import sys
import types
import unittest
from unittest.mock import patch

import windows_public_video


class WindowsPublicVideoTests(unittest.TestCase):
    def test_public_video_search_only_constructs_watch_urls_from_valid_ids(self):
        seen=[]
        class FakeYoutubeDL:
            def __init__(self,opts):
                seen.append(opts)
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def extract_info(self,target,download):
                seen.append((target,download))
                return {"entries":[{"id":"aaaaaaaaaaa","title":"Example","channel":"Demo"},
                                   {"id":"bad/secret","title":"Should be dropped"},
                                   {"id":"bbbbbbbbbbb","title":"Second"}]}
        with patch.dict(sys.modules,{"yt_dlp":types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)}):
            response=windows_public_video.search("popular technology")
        self.assertTrue(response["ok"])
        self.assertEqual(response["channel"],"youtube")
        self.assertEqual(response["action"],"search")
        self.assertEqual(len(response["items"]),2)
        self.assertEqual(response["items"][0]["url"],"https://www.youtube.com/watch?v=aaaaaaaaaaa")
        self.assertEqual(seen[-1],("ytsearch5:popular technology",False))
        self.assertTrue(seen[0]["skip_download"])
        self.assertFalse(seen[0]["cachedir"])

    def test_input_cannot_smuggle_flags_or_urls_into_ytsearch(self):
        for query in ("--cookies", "https://127.0.0.1/private", "x\n--paths", "z"):
            with self.assertRaises(Exception):
                windows_public_video.search(query)

    def test_backend_failure_never_falsely_verifies_result(self):
        class FakeYoutubeDL:
            def __init__(self,opts):pass
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def extract_info(self,target,download):
                raise RuntimeError("YouTube blocked")
        with patch.dict(sys.modules,{"yt_dlp":types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)}):
            result=windows_public_video.search("robot technology")
        self.assertFalse(result["ok"])
        self.assertEqual(result["items"],[])
        self.assertNotIn("cookie",str(result).lower())


if __name__=="__main__":
    unittest.main()
