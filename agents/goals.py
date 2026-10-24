"""Per-portal start URLs and natural-language goals for the TinyFish Agent API."""
from datetime import datetime, timedelta

from agents.schemas import IST

_COMMON = (
    "Today is {today}. Only keep bids closing on or after {min_close} (skip anything closing sooner). "
    "Relevance: keep a bid only if its items/title describe '{category}' itself or a close variant; "
    "never keep a bid just because the ministry, department or organisation name contains a search word. "
    "Return at most {limit} bids. "
    "If a captcha, OTP or login wall appears, do NOT try to solve it: stop immediately and "
    "return blocked=true with blocked_reason and an empty bids list. "
    "blocked is ONLY for captcha/login/site errors: if the site works but nothing relevant is found, "
    "return blocked=false and an empty bids list. "
    "This is read-only: never click submit, participate, or bid buttons. "
    "Copy values exactly as shown (end_date with its time, est_value in INR as shown); "
    "use absolute URLs for doc_url; use an empty string for anything not shown."
)

PORTALS = {
    "gem": {
        "url": "https://bidplus.gem.gov.in/all-bids",
        "goal": (
            "Type '{category}' into the bid search box and run the search. "
            "If page 1 has no relevant bids, search once more using only the last word of '{category}' "
            "(e.g. 'rack') and apply the same relevance rule. "
            "If the results allow sorting, sort by bid end date with the latest closing first. "
            "For each bid card read: bid_no (e.g. GEM/2026/B/1234567), "
            "title (the FULL 'Items' text; if it is cut off with '...', read the full text from its tooltip "
            "or title attribute, or open the bid details, and never return a value ending in '...'), "
            "item (the main product name), buyer (ministry and department), quantity, "
            "end_date (the 'End Date' value with time), "
            "doc_url (copy the exact href of the bid-number link, which opens the bid document and looks like "
            "https://bidplus.gem.gov.in/showbidDocument/1234567; never build or guess a URL, use an empty string "
            "if you can't read the href). "
            "Look through up to 3 result pages to find matching bids. " + _COMMON
        ),
    },
    "cppp": {
        "url": "https://eprocure.gov.in/eprocure/app?page=FrontEndLatestActiveTenders&service=page",
        "goal": (
            "On the CPPP 'Latest Active Tenders' list, find tenders related to '{category}' "
            "(match on title). For each matching row read: bid_no (the tender reference number / tender ID), "
            "title, buyer (organisation name), end_date (bid submission closing date), "
            "doc_url (absolute link of the tender title). " + _COMMON
        ),
    },
    "haryana": {
        "url": "https://etenders.hry.nic.in/nicgep/app",
        "goal": (
            "On the Haryana e-procurement home page, open the 'Latest Tenders' list (use 'more' if shown) "
            "and find tenders related to '{category}' (match on title). For each matching row read: "
            "bid_no (tender reference number or tender ID), title, buyer (department), "
            "end_date (closing date), doc_url (absolute link of the tender title). "
            "If the only way to search is a form with a captcha, return what is visible in the latest list instead. "
            + _COMMON
        ),
    },
}


def goal_for(portal: str, category: str, limit: int = 10, min_days: int = 2) -> tuple[str, str]:
    p = PORTALS[portal]
    today = datetime.now(IST).date()
    goal = p["goal"].format(
        category=category, limit=limit, today=today.strftime("%d %b %Y"),
        min_close=(today + timedelta(days=min_days)).strftime("%d %b %Y"),
    )
    return p["url"], goal
