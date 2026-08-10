"""Shared config and helpers for the PMCPA scrape."""

import json
import pathlib
import re
import time
import urllib.error
import urllib.request

# robots.txt only names user-agent "cludo" (Disallow: /settings/, Crawl-delay: 5).
# We honour the crawl-delay anyway and stay off /settings/.
CRAWL_DELAY = 5.0
USER_AGENT = (
    "PMCPA-research-scraper/1.0 "
    "(academic research on ABPI Code cases; contact: tim.nelson@aixplain.com)"
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HTML_DIR = DATA / "html"
LOG_DIR = DATA / "logs"

CASE_URLS = DATA / "case_urls.jsonl"
MANIFEST = DATA / "manifest.jsonl"
FAILURES = LOG_DIR / "failures.log"

# The completed-cases listing is rendered client-side by Cludo search.
# Settings are inlined in the page source at /cases/completed-cases/.
CLUDO_CUSTOMER_ID = 2562
CLUDO_ENGINE_ID = 11712  # cases engine (11704 is the general site engine)
CLUDO_API = f"https://api.cludo.com/api/v3/{CLUDO_CUSTOMER_ID}/{CLUDO_ENGINE_ID}/search"


def cludo_auth_header():
    """Cludo's public JS builds this as base64("customerId:engineId:SearchKey")."""
    import base64

    raw = f"{CLUDO_CUSTOMER_ID}:{CLUDO_ENGINE_ID}:SearchKey"
    return "SiteKey " + base64.b64encode(raw.encode()).decode()


# The site's own "Case number" field is not clean. Alongside the usual
# AUTH/3048/6/18 it contains multi-case reports ("AUTH/1806/3/06 and
# AUTH/1809/3/06", up to five at once), separator variants (and / & / , / -),
# party names appended ("AUTH/3134/12/18 Complainant v Shield"), a space
# instead of a slash ("CASE 0277/08/24"), a missing slash ("AUTH2024/7/07"),
# and trailing tabs. So we parse case numbers out rather than string-munge.
CASE_NUM_RE = re.compile(
    r"\b([A-Z]{3,})\s*/?\s*(\d{2,5})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\b"
)


def parse_case_numbers(text):
    """Extract normalised case numbers from a string, in order, deduped.

    'AUTH/1806/3/06 and AUTH/1809/3/06' -> ['AUTH-1806-3-06', 'AUTH-1809-3-06']
    'CASE 0277/08/24'                   -> ['CASE-0277-08-24']
    """
    found = []
    for m in CASE_NUM_RE.finditer(text or ""):
        norm = "-".join(m.group(1, 2, 3, 4))
        if norm not in found:
            found.append(norm)
    return found


def case_number_to_filename(case_number):
    """CASE/0748/09/25 -> CASE-0748-09-25.html

    A report covering several cases is named for all of them, joined by '__',
    so the mapping stays unambiguous and reversible.
    Returns None if no case number can be parsed.
    """
    nums = parse_case_numbers(case_number)
    if not nums:
        return None
    return "__".join(nums) + ".html"


def log_failure(stage, ident, message):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{utc_now()}\t{stage}\t{ident}\t{message}\n"
    with FAILURES.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(f"  FAIL [{stage}] {ident}: {message}")


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path):
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def fetch(url, data=None, headers=None, timeout=60, retries=2, backoff=10.0):
    """GET/POST returning (status, body_bytes, final_url).

    Retries transient network errors (the site throws the odd ECONNRESET --
    observed ~0.7% of requests, and the same URL succeeds on the next attempt).
    HTTP error responses are returned, not retried, so they land in the
    manifest. Raises only if every attempt hits a network error.
    """
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept-Language", "en-GB,en;q=0.9")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(), resp.geturl()
        except urllib.error.HTTPError as exc:
            # Keep the body: error pages are still evidence worth recording.
            return exc.code, exc.read(), url
        except Exception as exc:  # noqa: BLE001 - URLError, socket errors, timeouts
            last = exc
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise last
