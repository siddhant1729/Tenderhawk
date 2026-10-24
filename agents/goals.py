"""Per-portal start URLs and natural-language goals for the TinyFish Agent API."""

_COMMON = (
    "Only keep bids whose closing date is after today. "
    "Return at most {limit} bids. Do not open more than 2 result pages. "
    "If a captcha, OTP or login wall appears, do NOT try to solve it: stop immediately and "
    "return blocked=true with blocked_reason and an empty bids list. "
    "This is read-only: never click submit, participate, or bid buttons. "
    "Copy values exactly as shown (end_date with its time, est_value in INR as shown); "
    "use absolute URLs for doc_url; use an empty string for anything not shown."
)

PORTALS = {
    "gem": {
        "url": "https://bidplus.gem.gov.in/all-bids",
        "goal": (
            "Search the GeM bid list for '{category}' using the search box and wait for results. "
            "For each bid card read: bid_no (e.g. GEM/2026/B/1234567), title (the items line), item, "
            "buyer (ministry and department), quantity, end_date (the 'End Date' value), "
            "doc_url (absolute link of the bid number, which opens the bid document). " + _COMMON
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


def goal_for(portal: str, category: str, limit: int = 10) -> tuple[str, str]:
    p = PORTALS[portal]
    return p["url"], p["goal"].format(category=category, limit=limit)
