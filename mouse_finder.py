"""A small, read-only MouseMarket RSS watcher. Python 3.10+, no dependencies."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ATOM = {"a": "http://www.w3.org/2005/Atom"}
FEED = "https://www.reddit.com/r/MouseMarket/new/.rss"
USER_AGENT = "python:mouse-finder:v1.1 (personal MouseMarket RSS reader)"
BASE = re.compile(r"\b(?:ulx|ultra[\s-]*light[\s-]*x)\b", re.I)
FAMILY = re.compile(r"\b(prophecy|sakura)\b", re.I)
# "Comp" is also everyday shorthand, so it names a mouse only beside ULX or Finalmouse.
COMPETITION = re.compile(r"\b(?:competition|comps?)\b", re.I)
FINALMOUSE = re.compile(r"\bfinal\s*mouse\b", re.I)
# Verified against https://finalmouse.com/collections/all?page=2.
PROPHECY_COLORS = ("Clix", "ScreaM", "Tarik", "Tfue")
COLOR_PATTERN = "|".join(re.escape(name) for name in PROPHECY_COLORS)
PROPHECY_COLOR = re.compile(rf"\b(?:{COLOR_PATTERN})\b", re.I)
# Tarik and ScreaM can describe older editions, so their names alone are ambiguous.
DISTINCT_PROPHECY_COLOR = re.compile(r"\b(?:clix|tfue)\b", re.I)
# A lone letter is a size, but not the 's' in "it's" / "Tfue's" or the 'm' in "I'm".
MEDIUM = re.compile(r"\b(?:medium|med|lion)\b|(?<![\w'’])m(?![\w'’])", re.I)
OTHER_SIZE = re.compile(r"\b(?:small|large|classic|cheetah|tiger)\b|(?<![\w'’])[sl](?![\w'’])", re.I)
OLD_EDITION = re.compile(
    r"\b(?:phantom|guardian|aceu|frostlord|starlight|slx)\b|\bpro\b|\bscream\s+one\b", re.I
)
SOLD = re.compile(r"\b(?:sold|pending|traded|unavailable)\b", re.I)
WANTED = re.compile(r"\b(?:looking\s+(?:to\s+(?:buy|purchase)|for)|wtb|want(?:ed|ing)?\s+to\s+buy)\b", re.I)
# Prefix matches also cover PPG&S, PayPalG&S, and CashApp.
PAYMENT = re.compile(r"\b(?:paypal|pp|cash|venmo|zelle|money|g\s*&\s*s|f\s*&\s*f)|\$", re.I)
# "[H] PayPal [W] ..." is a buyer, even when [W] also mentions a price.
BUYER_HAVE = re.compile(r"^\W*(?:local\s+)?(?:paypal|pp|cash|venmo|zelle|money|\$)", re.I)
NTFY_SERVER = "https://ntfy.sh"
NTFY_TOPIC = re.compile(r"[-_A-Za-z0-9]{1,64}")
MAX_PUSHES = 5
# Consecutive failed checks before a push warns that the watcher is not working.
FAILURE_ALERT_AFTER = 6


@dataclass(frozen=True)
class Post:
    id: str
    title: str
    body: str
    url: str
    created: datetime


@dataclass(frozen=True)
class Match:
    post: Post
    confidence: str
    reason: str
    evidence: tuple[str, ...]

    @property
    def key(self) -> str:
        # Persist only digests for deduplication, never post bodies or usernames.
        value = json.dumps([self.post.id, self.confidence, self.evidence])
        return hashlib.sha256(value.encode()).hexdigest()


class BodyParser(HTMLParser):
    """Keep HTML table rows intact and preserve struck-out item names."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"p", "div", "li", "tr", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag in {"del", "s", "strike"}:
            self.parts.append("~~")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")
        elif tag in {"del", "s", "strike"}:
            self.parts.append("~~")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def body_text(content: str) -> str:
    parser = BodyParser()
    parser.feed(content.split("<!-- SC_ON -->", 1)[0])
    return "\n".join(" ".join(line.split()).strip(" |")
                     for line in "".join(parser.parts).splitlines() if line.strip(" |"))


def parse_feed(payload: bytes) -> list[Post]:
    root = ET.fromstring(payload)
    if root.tag != "{http://www.w3.org/2005/Atom}feed":
        raise ValueError("Expected an Atom feed; Reddit may have returned a block page.")
    posts = []
    for entry in root.findall("a:entry", ATOM):
        identity = entry.findtext("a:id", "", ATOM)
        if not re.fullmatch(r"t3_[a-z0-9]+", identity):
            continue
        stamp = entry.findtext("a:published", "", ATOM) or entry.findtext("a:updated", "", ATOM)
        created = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("Feed timestamp has no timezone.")
        # Construct the link from the validated ID, rather than trusting feed URLs.
        posts.append(Post(identity, entry.findtext("a:title", "", ATOM),
                          body_text(entry.findtext("a:content", "", ATOM)),
                          "https://www.reddit.com/comments/" + identity[3:], created))
    return posts


def fragments(text: str) -> list[str]:
    # Commas separate inventory items. A slash between sizes stays with its item.
    return [part.strip(" -*|") for part in re.split(
        rf"\n|[,;]|\s+&\s+|\s+and\s+|\s*/\s*(?=(?:finalmouse|ulx|ultralight|sakura|prophecy|{COLOR_PATTERN})\b)",
        text, flags=re.I,
    ) if part.strip(" -*|")]


def title_sections(title: str) -> tuple[str, str] | None:
    tags = re.search(r"\[\s*H\s*\](.*?)\[\s*W\s*\](.*)$", title, re.I)
    return (tags.group(1).strip(), tags.group(2).strip()) if tags else None


def family_of(text: str) -> str:
    families = {found.lower() for found in FAMILY.findall(text)}
    if PROPHECY_COLOR.search(text) and not OLD_EDITION.search(text):
        families.add("prophecy")
    if COMPETITION.search(text):
        families.add("competition")
    return next(iter(families)) if len(families) == 1 else "generic"


def is_model(text: str) -> bool:
    return bool(BASE.search(text) or FAMILY.search(text) or PROPHECY_COLOR.search(text)
                or (COMPETITION.search(text) and FINALMOUSE.search(text)))


def sold_item(text: str) -> bool:
    # Do not count a row as sold just because its quantity changed from ~~2~~ to 1.
    unstruck = re.sub(r"~~.*?~~", "", text)
    if is_model(text) and not is_model(unstruck):
        return True
    # Treat 'not sold' / 'unsold' / 'sold out on the website' as ambiguous instead.
    cleaned = re.sub(r"\bnot\s+(?:sold|pending)\b|\bsold\s+out\b", "", unstruck, flags=re.I)
    return bool(SOLD.search(cleaned))


def classify(post: Post, *, strict: bool = False) -> Match | None:
    sections = title_sections(post.title)
    # This watcher is for cash sales, rather than trades for a different mouse.
    if not sections or not PAYMENT.search(sections[1]):
        return None
    offer, want = sections
    # A generic seller title ("[H] Finalmouse collection") may list the mouse only in the body.
    # Keywords in [W] or a buyer's body must never qualify a post.
    if not is_model(offer) and (BUYER_HAVE.search(offer) or is_model(want)):
        return None
    if re.search(r"\[(?:sold|closed|traded)\]", post.title, re.I):
        return None
    if re.fullmatch(r"\s*(?:sold|all\s+sold|everything\s+sold)[.!\s]*", post.body, re.I):
        return None
    if re.search(r"(?im)^\s*(?:all|everything)\s+(?:is\s+)?(?:sold|traded)[.!\s]*$", post.body):
        return None

    title_items = [part for part in fragments(offer) if is_model(part)]
    body_items = [part for part in fragments(post.body) if is_model(part) and not WANTED.search(part)]
    candidates = list(body_items)
    # Body rows are more specific and can mark a title item as sold or wrong-size.
    for item in title_items:
        family = family_of(item)
        if not any(family_of(row) == family for row in body_items):
            candidates.append(item)
    # An inventory-heading-only mention is still useful if the body has no rows.
    if not candidates:
        candidates = title_items

    accepted: list[tuple[int, str, str]] = []
    for item in candidates:
        if sold_item(item):
            continue
        explicit = bool(FAMILY.search(item))
        competition = bool(COMPETITION.search(item))
        if not explicit and not competition and OLD_EDITION.search(item):
            continue
        color = PROPHECY_COLOR.search(item)
        same_family = [part for part in title_items if family_of(part) == family_of(item)]
        # A shorthand body row can rely on its unambiguous Prophecy title.
        if not explicit and color and len(same_family) == 1:
            title_color = PROPHECY_COLOR.search(same_family[0])
            same_color = not title_color or title_color.group().lower() == color.group().lower()
            if same_color and re.search(r"\bprophecy\b", same_family[0], re.I):
                explicit = True
        inferred = bool(DISTINCT_PROPHECY_COLOR.search(item))
        target_edition = explicit or inferred or competition
        medium = bool(MEDIUM.search(item))
        other = bool(OTHER_SIZE.search(item))
        # A uniquely identified title item can supply a size omitted in its row.
        if target_edition and not medium and not other and len(same_family) == 1:
            medium = bool(MEDIUM.search(same_family[0]))
            other = bool(OTHER_SIZE.search(same_family[0]))
        if other and not medium:
            continue
        if target_edition and medium and not other:
            reason = ("Prophecy/Sakura with an explicit medium size" if explicit else
                      "Competition with an explicit medium size" if competition else
                      f"Prophecy inferred from {color.group()}; explicit medium size")
            accepted.append((2, reason, item))
        elif target_edition:
            accepted.append((1, "Target edition; confirm size with seller", item))
        elif color:
            accepted.append((1, f"{color.group()} may be Prophecy or an older edition; confirm edition and size", item))
        else:
            accepted.append((1, "ULX mentioned; confirm Prophecy/Sakura/Competition edition and medium size", item))
    if not accepted:
        return None
    rank = max(item[0] for item in accepted)
    if strict and rank < 2:
        return None
    reason = next(item[1] for item in accepted if item[0] == rank)
    evidence = tuple(dict.fromkeys(item[2] for item in accepted))
    return Match(post, "Strong match" if rank == 2 else "Possible match", reason, evidence)


def page_delay(headers) -> float:
    # Logged-out RSS allows about one request per minute, so wait out the window Reddit reports.
    try:
        if float(headers.get("x-ratelimit-remaining", 1)) >= 1:
            return 2.0
        return min(float(headers.get("x-ratelimit-reset", 60)) + 1, 120.0)
    except (TypeError, ValueError):
        return 2.0


def fetch_posts(cutoff: datetime, max_pages: int) -> tuple[list[Post], list[str]]:
    posts: dict[str, Post] = {}
    warnings: list[str] = []
    after = None
    delay = 0.0
    for page in range(max_pages):
        query = {"limit": "100"}
        if after:
            query["after"] = after
            time.sleep(delay)
        request = urllib.request.Request(FEED + "?" + urllib.parse.urlencode(query), headers={
            "User-Agent": USER_AGENT, "Accept": "application/atom+xml",
        })
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = response.read(4_000_001)
                if len(payload) > 4_000_000:
                    raise ValueError("Feed exceeded the 4 MB size limit.")
                delay = page_delay(response.headers)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403, 429}:
                if posts:
                    # Keep the newer pages rather than discarding a partly successful check.
                    warnings.append(f"Reddit returned HTTP {exc.code} for an older page; "
                                    "older posts in the requested time window may be missing.")
                    break
                raise RuntimeError(
                    f"Reddit returned HTTP {exc.code}. RSS access is blocked or limited. "
                    "No matches can be determined. No bypass or automatic retries attempted."
                ) from exc
            raise
        batch = parse_feed(payload)
        new_ids = {post.id for post in batch} - posts.keys()
        if batch and not new_ids:
            warnings.append("Reddit repeated a feed page; coverage may be incomplete.")
            break
        posts.update((post.id, post) for post in batch)
        if not batch or min(post.created for post in batch) <= cutoff or len(batch) < 100:
            break
        after = batch[-1].id
    else:
        warnings.append("Page limit reached; older posts in the requested time window may be missing.")
    return sorted((post for post in posts.values() if post.created >= cutoff),
                  key=lambda post: post.created, reverse=True), warnings


def read_state(path: Path) -> dict:
    if not path.exists():
        return {"seen": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or not isinstance(state.get("seen"), dict):
        raise ValueError("Invalid state file; rename it to reset deduplication.")
    return state


def write_report(path: Path, matches: list[Match], new_keys: set[str], warnings: list[str],
                 checked: datetime, scanned: int, hours: float) -> None:
    escape = html.escape
    cards = []
    for match in matches:
        badge = "New / updated" if match.key in new_keys else "Seen before"
        evidence = "".join("<li>" + escape(line) + "</li>" for line in match.evidence)
        cards.append(f"""<article><div class="badge">{escape(match.confidence)} · {badge}</div>
<h2><a href="{escape(match.post.url, quote=True)}" rel="noreferrer">{escape(match.post.title)}</a></h2>
<p>{escape(match.reason)}</p><ul>{evidence}</ul>
<small>Posted {match.post.created.strftime('%Y-%m-%d %H:%M UTC')}</small></article>""")
    notices = "".join("<p class='warning'>" + escape(warning) + "</p>" for warning in warnings)
    content = "\n".join(cards) or "<article>No matching listings found in this check.</article>"
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Mouse Finder · MouseMarket</title><style>
body{{font:16px/1.6 system-ui,sans-serif;background:#111722;color:#e6ecf5;max-width:960px;margin:40px auto;padding:0 24px}}
h1{{line-height:1.2}}h2{{font-size:18px}}a{{color:#9ccaff}}article{{background:#1c2635;border:1px solid #35465f;border-radius:12px;padding:22px;margin:18px 0}}
.badge{{color:#b6e9ce;font-weight:600}}small,.muted{{color:#b1bfd2}}.warning{{color:#ffda98}}
</style><h1>Mouse Finder</h1><p>Medium ULX · Prophecy, Sakura, or Competition · r/MouseMarket</p>
<p class="muted">Checked {checked.strftime('%Y-%m-%d %H:%M UTC')} · {scanned} posts scanned · Last {hours:g} hours</p>
{notices}{content}<p class="muted">This is a snapshot of the RSS feed. Sale flair is unavailable.
Check the original listing for photos, size, edition, price, and current availability.
Listings sold between checks or described only in images may be missed.</p></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


def append_log(path: Path | None, line: str) -> None:
    # Scheduled runs use pythonw, which has no console, so this is their only output.
    if path:
        with contextlib.suppress(OSError), path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now():%Y-%m-%d %H:%M} {' '.join(line.split())}\n")


def notify(topic: str, title: str, message: str, *, click: str | None = None, priority: int = 3) -> None:
    # The JSON form avoids HTTP-header encoding limits for emoji and non-Latin titles.
    payload = {"topic": topic, "title": title, "message": message, "priority": priority, "tags": ["computer_mouse"]}
    if click:
        payload["click"] = click
    request = urllib.request.Request(NTFY_SERVER, data=json.dumps(payload).encode(), method="POST", headers={
        "Content-Type": "application/json", "User-Agent": USER_AGENT,
    })
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


def push_matches(topic: str, matches: list[Match]) -> set[str]:
    """Push each new match; return the keys that failed so the next check retries them."""
    failed = set()
    # Tapping a push opens the listing, so Strong matches go first.
    ordered = sorted(matches, key=lambda match: match.confidence != "Strong match")
    for match in ordered[:MAX_PUSHES]:
        try:
            notify(topic, f"{match.confidence} · r/MouseMarket",
                   f"{match.post.title}\n\n" + "\n".join(match.evidence) + f"\n\n{match.reason}",
                   click=match.post.url, priority=4 if match.confidence == "Strong match" else 3)
        except OSError:
            failed.add(match.key)
    extra = ordered[MAX_PUSHES:]
    if extra:
        try:
            notify(topic, f"{len(extra)} more new matches", "Open matches.html on your PC to see them all.")
        except OSError:
            failed.update(match.key for match in extra)
    return failed


def report_failure(args: argparse.Namespace, state: dict | None, error: str) -> int:
    # Never turn a blocked/error response into a successful zero-match check.
    print(f"Check failed: {error}\nThis run did not finish successfully; check the report timestamp.", file=sys.stderr)
    append_log(args.log, "FAILED: " + error)
    if state is None:
        return 1
    count = state.get("failures")
    state["failures"] = (count if isinstance(count, int) else 0) + 1
    # A watcher that silently stops looks the same as a quiet market, so say so once per outage.
    if args.ntfy_topic and state["failures"] >= FAILURE_ALERT_AFTER and not state.get("failure_alerted"):
        with contextlib.suppress(OSError):
            notify(args.ntfy_topic, "Mouse Finder checks are failing",
                   f"The last {state['failures']} checks failed, so new listings are not being seen.\n{error}")
            state["failure_alerted"] = True
    with contextlib.suppress(OSError):
        write_state(args.state, state)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=48, help="Lookback window (default: 48 hours)")
    parser.add_argument("--max-pages", type=int, default=5, help="At most this many 100-post feeds (default: 5)")
    parser.add_argument("--strict", action="store_true", help="Require Prophecy/Sakura/Competition (or Clix/Tfue) AND medium size")
    parser.add_argument("--open", action="store_true", help="Open the report in your browser")
    parser.add_argument("--open-on-new", action="store_true", help="Open the report only for new or updated matches")
    parser.add_argument("--ntfy-topic", help="Push new matches, and repeated failures, to this ntfy.sh topic")
    parser.add_argument("--test-notification", action="store_true", help="Send one test push to --ntfy-topic and exit")
    parser.add_argument("--log", type=Path, help="Append a one-line summary of each run to this file")
    parser.add_argument("--input", type=Path, help="Read an Atom XML file instead of making network requests")
    parser.add_argument("--report", type=Path, default=ROOT / "matches.html")
    parser.add_argument("--state", type=Path, default=ROOT / ".mouse-finder-state.json")
    args = parser.parse_args(argv)
    if not 0 < args.hours <= 720 or not 1 <= args.max_pages <= 10:
        parser.error("--hours must be between 0 and 720; --max-pages must be between 1 and 10")
    if args.ntfy_topic is not None and not NTFY_TOPIC.fullmatch(args.ntfy_topic):
        parser.error("--ntfy-topic may contain only letters, digits, '-' and '_' (at most 64)")
    if args.test_notification:
        if not args.ntfy_topic:
            parser.error("--test-notification requires --ntfy-topic")
        try:
            notify(args.ntfy_topic, "Mouse Finder test", "Phone alerts are working.")
        except OSError as exc:
            print(f"Test notification failed: {exc}", file=sys.stderr)
            return 1
        print("Test notification sent.")
        return 0
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=args.hours)
    try:
        state = read_state(args.state)
    except (OSError, ValueError) as exc:
        return report_failure(args, None, str(exc))
    try:
        if args.input:
            posts = [post for post in parse_feed(args.input.read_bytes()) if post.created >= cutoff]
            warnings = ["Offline input: this report is not a live Reddit check."]
        else:
            posts, warnings = fetch_posts(cutoff, args.max_pages)
        matches = [match for post in posts if (match := classify(post, strict=args.strict))]
        new = [match for match in matches if match.key not in state["seen"]]
        new_keys = {match.key for match in new}
        write_report(args.report, matches, new_keys, warnings, now, len(posts), args.hours)
    except (OSError, ValueError, ET.ParseError, RuntimeError) as exc:
        return report_failure(args, state, str(exc))
    unsent = push_matches(args.ntfy_topic, new) if args.ntfy_topic and new else set()
    if state.get("failure_alerted") and args.ntfy_topic:
        with contextlib.suppress(OSError):
            notify(args.ntfy_topic, "Mouse Finder is working again", "Checks are succeeding again.")
    # Retain anonymous digests for 30 days, so overlapping checks do not re-alert.
    threshold = (now - timedelta(days=30)).timestamp()
    seen = {key: stamp for key, stamp in state["seen"].items()
            if isinstance(stamp, (float, int)) and stamp > threshold}
    # Keep first-seen times, so a quiet check leaves the state file unchanged (no cloud commit).
    # A match whose push failed stays unseen, so the next check sends it again.
    seen.update({match.key: now.timestamp() for match in new if match.key not in unsent})
    try:
        write_state(args.state, {"seen": seen, "failures": 0, "failure_alerted": False})
    except OSError as exc:
        return report_failure(args, None, str(exc))
    print(f"Scanned {len(posts)} posts: {len(matches)} matches ({len(new)} new/updated).")
    for match in matches:
        print(f"[{match.confidence}] {match.post.title}\n  {match.post.url}")
    if unsent:
        warnings.append(f"{len(unsent)} push notification(s) failed; the next check will retry them.")
    for warning in warnings:
        print("WARNING: " + warning, file=sys.stderr)
    print("Report: " + str(args.report.resolve()))
    append_log(args.log, f"{len(posts)} posts, {len(matches)} matches, {len(new)} new"
               + "".join(f" | WARNING: {warning}" for warning in warnings))
    if args.open or (args.open_on_new and new_keys):
        webbrowser.open(args.report.resolve().as_uri())
    return 1 if unsent else 0


if __name__ == "__main__":
    raise SystemExit(main())
