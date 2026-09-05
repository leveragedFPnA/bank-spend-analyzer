import re
import pandas as pd


MERCHANT_PROFILES = [
    {
        "merchant": "IRCTC",
        "category": "Travel",
        "aliases": [
            "irctc",
            "irctc ticketing",
            "indian railway",
            "railway ticket",
        ],
    },
    {
        "merchant": "Uber",
        "category": "Transport",
        "aliases": [
            "uber",
            "uber india systems",
            "uberindia",
        ],
    },
    {
        "merchant": "Uber Eats",
        "category": "Food & Dining",
        "aliases": [
            "uber eats",
            "ubereats",
        ],
    },
    {
        "merchant": "District Dining",
        "category": "Food & Dining",
        "aliases": [
            "district dining",
            "districtdining",
            "districtdining1",
        ],
    },
    {
        "merchant": "Swiggy",
        "category": "Food & Dining",
        "aliases": [
            "swiggy",
        ],
    },
    {
        "merchant": "Zomato",
        "category": "Food & Dining",
        "aliases": [
            "zomato",
        ],
    },
    {
        "merchant": "Amazon",
        "category": "Shopping",
        "aliases": [
            "amazon",
            "amzn",
        ],
    },
    {
        "merchant": "Flipkart",
        "category": "Shopping",
        "aliases": [
            "flipkart",
        ],
    },
    {
        "merchant": "Myntra",
        "category": "Shopping",
        "aliases": [
            "myntra",
        ],
    },
    {
        "merchant": "Airtel",
        "category": "Bills & Utilities",
        "aliases": [
            "airtel",
        ],
    },
    {
        "merchant": "Jio",
        "category": "Bills & Utilities",
        "aliases": [
            "reliance jio",
            "jio",
        ],
    },
    {
        "merchant": "Netflix",
        "category": "Subscriptions",
        "aliases": [
            "netflix",
        ],
    },
    {
        "merchant": "Spotify",
        "category": "Subscriptions",
        "aliases": [
            "spotify",
        ],
    },
    {
        "merchant": "Blinkit",
        "category": "Groceries",
        "aliases": [
            "blinkit",
        ],
    },
    {
        "merchant": "Zepto",
        "category": "Groceries",
        "aliases": [
            "zepto",
        ],
    },
    {
        "merchant": "DMart",
        "category": "Groceries",
        "aliases": [
            "dmart",
            "avenue supermarts",
        ],
    },
]


CATEGORY_SIGNALS = {
    "Food & Dining": {
        "dining": 5,
        "restaurant": 5,
        "food": 4,
        "cafe": 4,
        "coffee": 3,
        "bakery": 3,
        "pizza": 3,
        "meal": 3,
    },
    "Travel": {
        "ticketing": 4,
        "railway": 5,
        "flight": 5,
        "airline": 5,
        "travel": 3,
        "bus": 3,
    },
    "Transport": {
        "cab": 4,
        "taxi": 4,
        "petrol": 5,
        "fuel": 5,
        "parking": 4,
        "metro": 4,
        "toll": 4,
        "fastag": 5,
    },
    "Bills & Utilities": {
        "electricity": 5,
        "broadband": 5,
        "recharge": 4,
        "water bill": 5,
        "gas bill": 5,
        "mobile bill": 5,
    },
    "Shopping": {
        "shopping": 4,
        "retail": 3,
        "mall": 3,
        "fashion": 3,
        "clothing": 3,
    },
    "Health": {
        "pharmacy": 5,
        "medical": 5,
        "medicine": 5,
        "hospital": 5,
        "clinic": 5,
        "diagnostic": 5,
    },
    "Subscriptions": {
        "subscription": 4,
        "membership": 4,
        "premium": 3,
    },
}


PROCESSING_NOISE = {
    "upi",
    "pos",
    "neft",
    "imps",
    "rtgs",
    "pinelabs",
    "pine labs",
    "razorpay",
    "payu",
    "payment",
    "payments",
    "india",
    "systems",
    "system",
    "ptsbi",
    "sbin",
}


def normalize_text(value):
    text = str(value).lower()
    text = re.sub(r"[^a-z0-9@]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact_text(value):
    return re.sub(r"[^a-z0-9]", "", normalize_text(value))


def matches_alias(text, alias):
    normalized_text = normalize_text(text)
    normalized_alias = normalize_text(alias)

    if not normalized_alias:
        return False

    exact_pattern = (
        rf"(?<![a-z0-9]){re.escape(normalized_alias)}"
        rf"(?![a-z0-9])"
    )

    if re.search(exact_pattern, normalized_text):
        return True

    compact_alias = compact_text(normalized_alias)
    compact_description = compact_text(normalized_text)

    return (
        len(compact_alias) >= 6
        and compact_alias in compact_description
    )


def identify_known_merchant(narration):
    for profile in MERCHANT_PROFILES:
        if any(
            matches_alias(narration, alias)
            for alias in profile["aliases"]
        ):
            return profile

    return None


def extract_unknown_merchant(narration):
    text = normalize_text(narration)

    for noise in PROCESSING_NOISE:
        text = re.sub(
            rf"(?<![a-z0-9]){re.escape(noise)}(?![a-z0-9])",
            " ",
            text,
        )

    words = [
        word
        for word in text.split()
        if not any(character.isdigit() for character in word)
        and len(word) > 1
    ]

    if not words:
        return "Unknown"

    return " ".join(words[:4]).title()


def infer_category_from_description(narration):
    text = normalize_text(narration)
    scores = {}
    matches = {}

    for category_name, signals in CATEGORY_SIGNALS.items():
        category_score = 0
        category_matches = []

        for signal, weight in signals.items():
            if matches_alias(text, signal):
                category_score += weight
                category_matches.append(signal)

        if category_score:
            scores[category_name] = category_score
            matches[category_name] = category_matches

    if not scores:
        return (
            "Other",
            "Low",
            "No merchant or category signal identified",
        )

    best_category = max(scores, key=scores.get)
    best_score = scores[best_category]
    matched_signals = ", ".join(matches[best_category][:3])

    confidence = "High" if best_score >= 5 else "Medium"

    return (
        best_category,
        confidence,
        f"Description matched: {matched_signals}",
    )


def classify_transaction(row):
    narration = str(row.get("Narration", ""))
    withdrawal = float(row.get("Withdrawal", 0) or 0)
    deposit = float(row.get("Deposit", 0) or 0)

    known_merchant = identify_known_merchant(narration)

    if known_merchant:
        return (
            known_merchant["merchant"],
            known_merchant["category"],
            "High",
            f"Recognized merchant: {known_merchant['merchant']}",
        )

    if deposit > 0 and withdrawal == 0:
        merchant = extract_unknown_merchant(narration)
        return (
            merchant,
            "Income",
            "Medium",
            "Deposit transaction",
        )

    if withdrawal <= 0:
        return (
            extract_unknown_merchant(narration),
            "Other",
            "Low",
            "No spending amount identified",
        )

    inferred_category, confidence, reason = (
        infer_category_from_description(narration)
    )

    return (
        extract_unknown_merchant(narration),
        inferred_category,
        confidence,
        reason,
    )


def classify_transactions(data):
    classified = data.apply(
        classify_transaction,
        axis=1,
        result_type="expand",
    )

    classified.columns = [
        "Merchant",
        "Category",
        "Confidence",
        "Classification Reason",
    ]

    return pd.concat(
        [
            data.reset_index(drop=True),
            classified.reset_index(drop=True),
        ],
        axis=1,
    )
