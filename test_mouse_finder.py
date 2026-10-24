import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import mouse_finder as mf


def post(offer="ULX Prophecy Scream Medium", body="", want="PayPal"):
    return mf.Post("t3_example", f"[US-CA] [H] {offer} [W] {want}", body,
                   "https://www.reddit.com/comments/example", datetime.now(timezone.utc))


def feed(title, body="", identity="t3_example"):
    from html import escape
    return f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>{identity}</id><title>{escape(title)}</title>
<published>{datetime.now(timezone.utc).isoformat()}</published>
<content type="html">{escape(body)}</content></entry></feed>'''.encode()


class FilteringTests(unittest.TestCase):
    def test_all_prophecy_color_names_are_recognized(self):
        for name in ["Clix", "ScreaM", "Tarik", "Tfue"]:
            with self.subTest(name=name):
                result = mf.classify(post(f"Finalmouse ULX Prophecy {name} M"), strict=True)
                self.assertEqual(result.confidence, "Strong match")

    def test_distinct_color_aliases_work_without_prophecy_or_ulx(self):
        for offer in ["ULX Clix M", "Finalmouse Tfue Medium", "clix med", "TFUE (Lion)"]:
            with self.subTest(offer=offer):
                result = mf.classify(post(offer), strict=True)
                self.assertEqual(result.confidence, "Strong match")

    def test_shared_color_aliases_require_edition_confirmation(self):
        for name in ["Tarik", "ScreaM"]:
            with self.subTest(name=name):
                result = mf.classify(post(f"{name} Medium"))
                self.assertEqual(result.confidence, "Possible match")
                self.assertIn("older edition", result.reason)
                self.assertIsNone(mf.classify(post(f"{name} Medium"), strict=True))

    def test_shared_color_body_uses_explicit_prophecy_title(self):
        for name in ["Tarik", "ScreaM"]:
            with self.subTest(name=name):
                result = mf.classify(post(f"ULX Prophecy {name} Medium", f"{name} | $175"), strict=True)
                self.assertEqual(result.confidence, "Strong match")

    def test_color_aliases_preserve_wrong_size_filter(self):
        for name in ["Clix", "ScreaM", "Tarik", "Tfue"]:
            with self.subTest(name=name):
                self.assertIsNone(mf.classify(post(f"{name} Classic")))

    def test_color_aliases_only_in_wanted_section_do_not_match(self):
        self.assertIsNone(mf.classify(post("PayPal", "Looking for Clix M", "Clix M")))

    def test_color_name_sold_body_overrides_prophecy_title(self):
        self.assertIsNone(mf.classify(post("ULX Prophecy Clix M", "~~Clix Medium~~ | $170")))

    def test_slash_separated_color_items_keep_sizes_separate(self):
        result = mf.classify(post("Clix Small / Tfue Medium"), strict=True)
        self.assertEqual(result.evidence, ("Tfue Medium",))

    def test_color_aliases_do_not_include_older_scream_one(self):
        self.assertIsNone(mf.classify(post("Finalmouse Scream One Medium")))

    def test_medium_aliases_and_ultralightx(self):
        for size in ["Medium", "MED", "(M)", "Lion"]:
            with self.subTest(size=size):
                result = mf.classify(post(f"Finalmouse UltralightX Sakura {size}"))
                self.assertEqual(result.confidence, "Strong match")

    def test_classic_and_other_sizes_are_not_medium(self):
        for size in ["Classic", "Tiger", "Large", "Small", "Cheetah", "(S)", "(L)"]:
            with self.subTest(size=size):
                self.assertIsNone(mf.classify(post(f"ULX Sakura {size}")))

    def test_wanted_section_does_not_match(self):
        self.assertIsNone(mf.classify(post("PayPal", "Looking for ULX Sakura medium", "ULX Sakura M")))
        self.assertIsNone(mf.classify(post("GPX medium", "ULX Sakura medium wanted", "ULX Sakura M")))

    def test_trade_only_is_not_a_cash_sale(self):
        self.assertIsNone(mf.classify(post(want="ULX Sakura Classic")))

    def test_unknown_size_is_possible_and_strict_excludes_it(self):
        self.assertEqual(mf.classify(post("ULX Prophecy Tfue")).confidence, "Possible match")
        self.assertIsNone(mf.classify(post("ULX Prophecy Tfue"), strict=True))

    def test_unspecified_edition_is_possible(self):
        result = mf.classify(post("ULX Medium"))
        self.assertEqual(result.confidence, "Possible match")
        self.assertIsNone(mf.classify(post("ULX Medium"), strict=True))

    def test_explicit_old_or_other_editions_are_excluded(self):
        for edition in ["Pro. Tarik", "Pro Scream", "Phantom", "Aceu", "Frostlord"]:
            with self.subTest(edition=edition):
                self.assertIsNone(mf.classify(post(f"ULX {edition} medium")))

    def test_real_competition_listing_is_strong(self):
        # r/MouseMarket, October 23, 2026.
        listing = mf.Post("t3_1xdr5q0", "[US-CA] [H] Finalmouse ULX Competition Medium [W] PayPal",
                          "Timestamp\nAsking $150 shipped\nNo QC issues",
                          "https://www.reddit.com/comments/1xdr5q0", datetime.now(timezone.utc))
        result = mf.classify(listing, strict=True)
        self.assertEqual(result.reason, "Competition with an explicit medium size")

    def test_competition_shorthands_are_recognized(self):
        for offer in ["ULX Comp M", "ULX Comps Medium", "2x ULX Comps (M)", "Finalmouse Comp Lion",
                      "UltralightX Competition Med"]:
            with self.subTest(offer=offer):
                self.assertEqual(mf.classify(post(offer), strict=True).confidence, "Strong match")

    def test_comp_without_ulx_or_finalmouse_is_not_a_mouse(self):
        for offer in ["Comp M", "Lamzu Atlantis Competition Medium", "GPX, comp grips M"]:
            with self.subTest(offer=offer):
                self.assertIsNone(mf.classify(post(offer)))

    def test_competition_keeps_size_and_sold_filters(self):
        self.assertIsNone(mf.classify(post("ULX Competition Small")))
        self.assertIsNone(mf.classify(post("ULX Comp M", "~~ULX Comp M~~ | $150 | SOLD")))
        self.assertEqual(mf.classify(post("ULX Competition")).reason, "Target edition; confirm size with seller")

    def test_competition_title_item_survives_other_body_rows(self):
        result = mf.classify(post("ULX Competition M, ULX Phantom M", "ULX Phantom M | $100"), strict=True)
        self.assertEqual(result.evidence, ("ULX Competition M",))

    def test_user_example_does_not_qualify(self):
        title = ("Finalmouse SLX, ULX Phantom, SL Phantom, SL Ares, SL Poseidon, "
                 "ULX Pro. Scream, ULX Pro. Tarik, ULX Aceu, OP1 8k")
        body = """|Finalmouse ULX Pro. Scream (Classic)|New|$230|1|
|Finalmouse ULX Pro. Tarik (Classic)|Slightly Used (2 hours)|$220|1|
|Finalmouse Starlight Ares (M)|Used|$220|1|
|Finalmouse ULX Aceu (Tiger)|Used|$180|1|
|Finalmouse ULX Phantom (Lion)|Used|$180|1|"""
        self.assertIsNone(mf.classify(post(title, body)))

    def test_body_size_does_not_bleed_from_another_mouse(self):
        self.assertIsNone(mf.classify(post("ULX Sakura, Starlight Ares", "ULX Sakura Small | $150\nStarlight Ares Medium | $200")))

    def test_unique_title_item_can_supply_size_missing_from_body(self):
        result = mf.classify(post("ULX Sakura Medium", "ULX Sakura | BNIB | $175"), strict=True)
        self.assertEqual(result.confidence, "Strong match")

    def test_multiple_title_items_do_not_supply_an_ambiguous_size(self):
        result = mf.classify(post("ULX Prophecy Tfue Medium, ULX Prophecy Clix Classic", "ULX Prophecy Clix | $175"))
        self.assertEqual(result.confidence, "Possible match")

    def test_sold_row_overrides_title(self):
        self.assertIsNone(mf.classify(post(body="~~ULX Prophecy Scream Medium~~ | $180 | SOLD")))

    def test_sold_other_item_does_not_hide_live_target(self):
        result = mf.classify(post("ULX Sakura Medium, GPX", "ULX Sakura Medium | $170\nGPX | SOLD"))
        self.assertEqual(result.confidence, "Strong match")

    def test_reduced_quantity_is_not_sold(self):
        result = mf.classify(post(body="ULX Prophecy Scream Medium | $180 | ~~2~~ 1"))
        self.assertEqual(result.confidence, "Strong match")

    def test_all_sold_is_excluded(self):
        self.assertIsNone(mf.classify(post(body="Timestamp\nULX Prophecy Medium $170\nALL SOLD")))

    def test_mixed_sizes_get_review_flag(self):
        self.assertEqual(mf.classify(post("ULX Sakura small / medium")).confidence, "Possible match")

    def test_pending_and_not_sold(self):
        self.assertIsNone(mf.classify(post(body="ULX Prophecy Medium | pending")))
        self.assertIsNotNone(mf.classify(post(body="ULX Prophecy Medium | not sold")))

    def test_payment_abbreviations_count_as_a_sale(self):
        for want in ["PP", "PPG&S", "PayPalG&S", "CashApp", "Cash App", "$", "G&S", "Local Cash"]:
            with self.subTest(want=want):
                result = mf.classify(post("ULX Sakura M", want=want))
                self.assertEqual(result.confidence, "Strong match")

    def test_real_buyer_wants_are_not_payments(self):
        # [W] sections from MouseMarket buying posts, October 2026.
        for want in ["Old finalmouse scream, ergo etc", "ULX Comp/Frost/Prophecy Small", "Pulsar X2F CrazyLight",
                     "Arye FCC1(carbon fiber one), Purple Demon1 Asus Harpe", "Lycan 8k, black, translucent white or pink.",
                     "Finalmouse M Prophecy Tarik, Scream, other ULX", "a small fingertip mouse"]:
            with self.subTest(want=want):
                self.assertIsNone(mf.classify(post("ULX Sakura M", want=want)))

    def test_generic_seller_title_uses_body_inventory(self):
        for offer in ["Finalmouse collection, Lamzu, Pulsar", "Mice", "Finalmouse mice $150+"]:
            with self.subTest(offer=offer):
                result = mf.classify(post(offer, "Finalmouse ULX Sakura (M) | BNIB | $250\nLamzu Atlantis | $60"))
                self.assertEqual(result.confidence, "Strong match")
                self.assertEqual(result.evidence, ("Finalmouse ULX Sakura (M) | BNIB | $250",))

    def test_generic_seller_title_still_filters_body_rows(self):
        self.assertIsNone(mf.classify(post("Finalmouse collection", "ULX Sakura Small | $200\nULX Phantom M | $180")))
        self.assertIsNone(mf.classify(post("Finalmouse collection", "~~ULX Sakura M~~ | $200")))

    def test_buyer_body_does_not_match_when_want_has_a_price(self):
        self.assertIsNone(mf.classify(post("PayPal", "Want a ULX Sakura in medium.", "ULX Sakura M, will pay $200")))
        self.assertIsNone(mf.classify(post("PP G&S", "Want a ULX Sakura in medium.", "Medium mouse, $200")))

    def test_generic_title_wanting_the_mouse_does_not_match(self):
        self.assertIsNone(mf.classify(post("GPX", "Would trade for a ULX Sakura M", "ULX Sakura M or PayPal")))

    def test_apostrophes_are_not_sizes(self):
        result = mf.classify(post("ULX Sakura M", "ULX Sakura - it's like new | $200"), strict=True)
        self.assertEqual(result.confidence, "Strong match")
        result = mf.classify(post("ULX Prophecy Tfue Medium", "Tfue's mouse, barely used | $220"), strict=True)
        self.assertEqual(result.confidence, "Strong match")
        self.assertEqual(mf.classify(post("ULX Sakura", "I'm selling my ULX Sakura")).confidence, "Possible match")


class FeedAndRunTests(unittest.TestCase):
    def test_html_table_rows_strikes_and_metadata(self):
        content = ("<!-- SC_OFF --><table><tr><td><del>ULX Sakura M</del></td><td>$170</td></tr>"
                   "<tr><td>ULX Prophecy M</td><td>~~2~~ 1</td></tr></table><!-- SC_ON --> /u/seller")
        parsed = mf.parse_feed(feed(post().title, content))[0]
        self.assertIn("~~ULX Sakura M~~ | $170", parsed.body)
        self.assertNotIn("/u/seller", parsed.body)
        result = mf.classify(parsed)
        self.assertEqual(result.confidence, "Strong match")
        self.assertEqual(len(result.evidence), 1)

    def test_rejects_block_page(self):
        with self.assertRaises(ValueError):
            mf.parse_feed(b"<html><body>Blocked</body></html>")

    def test_report_escapes_content(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "report.html"
            match = mf.classify(post("ULX Sakura M <script>alert(1)</script>"))
            mf.write_report(destination, [match], set(), [], datetime.now(timezone.utc), 1, 48)
            contents = destination.read_text(encoding="utf-8")
            self.assertNotIn("<script>", contents)
            self.assertIn("&lt;script&gt;", contents)

    def test_first_check_then_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sample = root / "feed.xml"
            sample.write_bytes(feed(post().title))
            arguments = ["--input", str(sample), "--report", str(root / "report.html"),
                         "--state", str(root / "state.json")]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(mf.main(arguments), 0)
            self.assertIn("New / updated", (root / "report.html").read_text())
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(mf.main(arguments), 0)
            self.assertIn("Seen before", (root / "report.html").read_text())
            state = (root / "state.json").read_text()
            self.assertNotIn("Scream", state)
            self.assertNotIn("example", state)

    def test_network_failure_keeps_previous_report_and_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report, state = root / "report.html", root / "state.json"
            report.write_text("old report")
            state.write_text('{"seen": {"abc": 1e12}}')
            with patch.object(mf, "fetch_posts", side_effect=RuntimeError("HTTP 403")):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(mf.main(["--report", str(report), "--state", str(state)]), 1)
            self.assertEqual(report.read_text(), "old report")
            saved = json.loads(state.read_text())
            self.assertEqual(saved["seen"], {"abc": 1e12})
            self.assertEqual(saved["failures"], 1)

    def test_rate_limit_is_not_treated_as_zero_matches(self):
        error = HTTPError(mf.FEED, 429, "Too many requests", {}, None)
        with patch.object(mf.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "429"):
                mf.fetch_posts(datetime.now(timezone.utc), 1)

    def test_pagination_uses_after_cursor(self):
        import html
        now = datetime.now(timezone.utc).isoformat()
        entries = "".join(f"<entry><id>t3_a{i}</id><title>{html.escape(post().title)}</title>"
                          f"<published>{now}</published></entry>" for i in range(100))
        first = f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode()
        second = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        with patch.object(mf.urllib.request, "urlopen", side_effect=[Response(first), Response(second)]) as request:
            with patch.object(mf.time, "sleep"):
                posts, warnings = mf.fetch_posts(datetime(2020, 1, 1, tzinfo=timezone.utc), 5)
        self.assertEqual(len(posts), 100)
        self.assertEqual(warnings, [])
        self.assertIn("after=t3_a99", request.call_args_list[1].args[0].full_url)

    def test_rate_limited_older_page_keeps_newer_posts_and_waits_for_reset(self):
        first = Response(full_page(), {"x-ratelimit-remaining": "0.0", "x-ratelimit-reset": "37"})
        error = HTTPError(mf.FEED, 429, "Too many requests", {}, None)
        with patch.object(mf.urllib.request, "urlopen", side_effect=[first, error]):
            with patch.object(mf.time, "sleep") as sleep:
                posts, warnings = mf.fetch_posts(datetime(2020, 1, 1, tzinfo=timezone.utc), 5)
        self.assertEqual(len(posts), 100)
        self.assertIn("HTTP 429", warnings[0])
        sleep.assert_called_once_with(38.0)

    def test_page_delay_is_capped_and_tolerates_bad_headers(self):
        self.assertEqual(mf.page_delay({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999"}), 120.0)
        self.assertEqual(mf.page_delay({"x-ratelimit-remaining": "95"}), 2.0)
        self.assertEqual(mf.page_delay({"x-ratelimit-remaining": "junk"}), 2.0)
        self.assertEqual(mf.page_delay({}), 2.0)


def full_page():
    import html
    now = datetime.now(timezone.utc).isoformat()
    entries = "".join(f"<entry><id>t3_a{i}</id><title>{html.escape(post().title)}</title>"
                      f"<published>{now}</published></entry>" for i in range(100))
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode()


class Response(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(payload)
        self.headers = headers or {}


class NotificationTests(unittest.TestCase):
    def run_main(self, root, *extra):
        sample = root / "feed.xml"
        if not sample.exists():
            sample.write_bytes(feed(post("ULX Sakura M").title))
        arguments = ["--input", str(sample), "--report", str(root / "report.html"),
                     "--state", str(root / "state.json"), "--log", str(root / "run.log"), *extra]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return mf.main(arguments)

    def test_new_match_is_pushed_once_with_listing_link(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(mf, "notify") as notify:
            root = Path(folder)
            self.assertEqual(self.run_main(root, "--ntfy-topic", "mouse-test"), 0)
            self.assertEqual(self.run_main(root, "--ntfy-topic", "mouse-test"), 0)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], "mouse-test")
        self.assertIn("ULX Sakura M", notify.call_args.args[2])
        self.assertEqual(notify.call_args.kwargs["click"], "https://www.reddit.com/comments/example")
        self.assertEqual(notify.call_args.kwargs["priority"], 4)

    def test_failed_push_is_retried_next_check(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(mf, "notify", side_effect=OSError("offline")):
                self.assertEqual(self.run_main(root, "--ntfy-topic", "mouse-test"), 1)
            with patch.object(mf, "notify") as notify:
                self.assertEqual(self.run_main(root, "--ntfy-topic", "mouse-test"), 0)
            notify.assert_called_once()
            self.assertIn("push notification(s) failed", (root / "run.log").read_text())

    def test_many_new_matches_are_capped_with_a_summary(self):
        from html import escape
        entries = "".join(f"<entry><id>t3_m{i}</id><title>{escape(post('ULX Sakura M').title)}</title>"
                          f"<published>{datetime.now(timezone.utc).isoformat()}</published></entry>" for i in range(8))
        with tempfile.TemporaryDirectory() as folder, patch.object(mf, "notify") as notify:
            root = Path(folder)
            (root / "feed.xml").write_bytes(f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode())
            self.run_main(root, "--ntfy-topic", "mouse-test")
        self.assertEqual(notify.call_count, mf.MAX_PUSHES + 1)
        self.assertEqual(notify.call_args.args[1], "3 more new matches")

    def test_repeated_failures_alert_once_then_recovery(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(mf, "notify") as notify:
            root = Path(folder)
            with patch.object(mf, "parse_feed", side_effect=ValueError("blocked")):
                for _ in range(mf.FAILURE_ALERT_AFTER + 2):
                    self.assertEqual(self.run_main(root, "--ntfy-topic", "mouse-test"), 1)
            self.assertEqual([call.args[1] for call in notify.call_args_list], ["Mouse Finder checks are failing"])
            self.assertEqual(self.run_main(root, "--ntfy-topic", "mouse-test"), 0)
            titles = [call.args[1] for call in notify.call_args_list]
            self.assertEqual(titles[1:], ["Strong match · r/MouseMarket", "Mouse Finder is working again"])
            self.assertEqual(json.loads((root / "state.json").read_text())["failures"], 0)
            self.assertIn("FAILED: blocked", (root / "run.log").read_text())

    def test_repeat_check_leaves_state_file_unchanged(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(mf, "notify"):
            root = Path(folder)
            self.run_main(root, "--ntfy-topic", "mouse-test")
            first = (root / "state.json").read_bytes()
            self.run_main(root, "--ntfy-topic", "mouse-test")
            self.assertEqual((root / "state.json").read_bytes(), first)

    def test_no_topic_means_no_pushes(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(mf, "notify") as notify:
            self.assertEqual(self.run_main(Path(folder)), 0)
        notify.assert_not_called()

    def test_notify_sends_json_to_ntfy(self):
        with patch.object(mf.urllib.request, "urlopen", return_value=Response(b"{}")) as request:
            mf.notify("mouse-test", "Strong match", "ULX Sakura M 🖱️", click="https://www.reddit.com/comments/x", priority=4)
        sent = request.call_args.args[0]
        self.assertEqual(sent.full_url, "https://ntfy.sh")
        self.assertEqual(sent.get_method(), "POST")
        self.assertEqual(json.loads(sent.data), {"topic": "mouse-test", "title": "Strong match",
                                                 "message": "ULX Sakura M 🖱️", "priority": 4,
                                                 "tags": ["computer_mouse"], "click": "https://www.reddit.com/comments/x"})

    def test_invalid_topic_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            mf.main(["--ntfy-topic", "bad topic/../x", "--test-notification"])


if __name__ == "__main__":
    unittest.main()
