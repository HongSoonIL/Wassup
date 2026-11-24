"""시험 주간 JSON과 설계서에 수록한 API·프롬프트 예시의 일관성을 검사한다."""

import json
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

DESIGN = Path(__file__).resolve().parent
SAMPLE = json.loads((DESIGN / "exam-week-sample.json").read_text(encoding="utf-8"))
SCENARIOS = {case["id"]: case for case in SAMPLE["scenarios"]}
POSTS = {post["post_id"]: post for post in SAMPLE["draft_candidates"]}


def examples(filename):
    markdown = (DESIGN / filename).read_text(encoding="utf-8")
    return [json.loads(block) for block in re.findall(r"^```json\n(.*?)\n```$", markdown, re.M | re.S)]


class ExamWeekContract(unittest.TestCase):
    def test_scenario_inputs(self):
        checkpoints = {point["id"] for point in SAMPLE["checkpoints"]}
        self.assertEqual(len(SCENARIOS), len(SAMPLE["scenarios"]))
        for case in SCENARIOS.values():
            with self.subTest(case=case["id"]):
                self.assertIn(case["checkpoint"], checkpoints)
        source = SAMPLE["generation_input"]
        calendar = {event["id"] for event in source["calendar"]}
        assets = {asset["photo_id"]: asset for asset in source["available_photos"]}
        for post in POSTS.values():
            self.assertLessEqual(set(post["calendar_ids"]), calendar)
            if post["photo_id"] is None:
                self.assertIn("missing_photo", post["issues"])
                continue
            asset = assets[post["photo_id"]]
            self.assertEqual(asset["character_id"], post["character_id"])
            self.assertTrue((DESIGN.parents[1] / asset["fixture_asset_path"]).is_file())

    def test_api_examples_share_one_published_version(self):
        feed, request, receipt, events = examples("exam-week-api.md")
        question = SCENARIOS["quoted_question"]["input"]
        self.assertEqual(request, question["message"])
        self.assertEqual(receipt["created_at"], question["at"])
        post = POSTS[request["source_post_id"]]
        public, = feed["posts"]
        self.assertEqual(public["id"], post["post_id"])
        for field in ("caption", "character_id", "version"):
            self.assertEqual(public[field], post[field])
        self.assertEqual(public["published_at"], post["publish_at"])
        self.assertEqual(public["photo"]["url"], f"/api/posts/{public['id']}/photo/")
        self.assertEqual(events[0]["message_id"], receipt["id"])
        self.assertEqual(events[-1]["message"]["created_at"], events[1]["expires_at"])
        self.assertNotEqual(receipt["id"], SAMPLE["private_memory"]["source_message"]["id"])

    def test_prompts_use_reviewed_photo_and_room_memory(self):
        week, generated, chat, reply = examples("exam-week-prompts.md")
        feed = examples("exam-week-api.md")[0]
        public, = feed["posts"]
        candidate, = generated["candidates"]
        self.assertEqual(week["week_start"], SAMPLE["week"]["week_start"])
        self.assertEqual(candidate["asset_key"], POSTS[public["id"]]["photo_id"])
        self.assertEqual(candidate["caption"], public["caption"])
        self.assertEqual(candidate["photo_alt"], public["photo"]["alt"])
        self.assertEqual(chat["quoted_post"], {"id": public["id"], "version": public["version"]})
        self.assertEqual(reply["used_posts"], [chat["quoted_post"]])
        memory = SAMPLE["private_memory"]
        self.assertEqual([item["id"] for item in chat["authorized_memories"]], [memory["id"]])
        self.assertEqual(reply["used_memory_ids"], [memory["id"]])
        for result in SCENARIOS["next_day"]["expect"]:
            allowed = [memory["id"]] if result["room_id"] == memory["room_id"] else []
            self.assertEqual(result["permitted_memory_ids"], allowed)
        after_delete = SCENARIOS["delete_before_delivery"]["steps"][-1]["expect"]
        self.assertEqual(after_delete["permitted_memory_ids"], [])

    def test_calendar_change_affects_only_dependent_reservations(self):
        points = {point["id"]: point for point in SAMPLE["checkpoints"]}
        approved = points["approved"]["expect"]["changed"]
        moved = points["rescheduled_event"]
        event_id = moved["input"]["calendar_id"]
        stale = {item["post_id"] for item in approved if event_id in POSTS[item["post_id"]]["calendar_ids"]}
        invalidated = moved["expect"]["invalidated_reservations"]
        retained = moved["expect"]["still_scheduled"]
        self.assertEqual(stale, {item["post_id"] for item in invalidated})
        self.assertEqual({item["post_id"] for item in approved} - stale, {item["post_id"] for item in retained})
        for item in moved["expect"]["changed"]:
            self.assertIn(item["post_id"], stale)
            self.assertEqual(item["state"], "draft")
            self.assertGreater(item["version"], POSTS[item["post_id"]]["version"])

    def test_scheduled_boundaries(self):
        post = POSTS[501]
        due = datetime.fromisoformat(post["publish_at"])
        before = SCENARIOS["before_release"]["input"]
        self.assertLess(datetime.fromisoformat(before["at"]), due)
        failed, retry, replay = SCENARIOS["failed_publication"]["steps"]
        self.assertEqual(failed["expect"]["public_post_ids"], [])
        self.assertGreater(datetime.fromisoformat(retry["expect"]["published_at"]), due)
        self.assertEqual(retry["expect"]["public_post_ids"], replay["expect"]["public_post_ids"])
        self.assertEqual(replay["expect"]["publication_count"], 1)
        first, followup, sent = SCENARIOS["first_deadline"]["steps"]
        deadline = datetime.fromisoformat(first["at"]) + timedelta(hours=3)
        self.assertEqual(first["expect"]["deadline"], followup["expect"]["deadline"])
        self.assertEqual(datetime.fromisoformat(followup["expect"]["deadline"]), deadline)
        self.assertLessEqual(datetime.fromisoformat(sent["at"]), deadline)


if __name__ == "__main__":
    unittest.main()
