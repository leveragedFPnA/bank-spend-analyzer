import re
import smtplib
from email.mime.text import MIMEText

import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="Personal Spend Analyzer",
    page_icon="₹",
    layout="wide",
)


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
        "aliases": ["swiggy"],
    },
    {
        "merchant": "Zomato",
        "category": "Food & Dining",
        "aliases": ["zomato"],
    },
    {
        "merchant": "Amazon",
        "category": "Shopping",
        "aliases": ["amazon", "amzn"],
    },
    {
        "merchant": "Flipkart",
        "category": "Shopping",
        "aliases": ["flipkart"],
    },
    {
        "merchant": "Myntra",
        "category": "Shopping",
        "aliases": ["myntra"],
    },
    {
        "merchant": "Airtel",
        "category": "Bills & Utilities",
        "aliases": ["airtel"],
    },
    {
        "merchant": "Jio",
        "category": "Bills & Utilities",
        "aliases": ["reliance jio", "jio"],
    },
    {
        "merchant": "Netflix",
        "category": "Subscriptions",
        "aliases": ["netflix"],
    },
    {
        "merchant": "Spotify",
        "category": "Subscriptions",
        "aliases": ["spotify"],
    },
    {
        "merchant": "Blinkit",
        "category": "Groceries",
        "aliases": ["blinkit"],
    },
    {
        "merchant": "Zepto",
        "category": "Groceries",
        "aliases": ["zepto"],
    },
    {
        "merchant": "DMart",
        "category": "Groceries",
        "aliases": ["dmart", "avenue supermarts"],
    },
]


CATEGORY_SIGNALS = {
    "Food & Dining": {
        "dining": 6,
        "restaurant": 6,
        "food": 5,
        "cafe": 5,
        "coffee": 4,
        "bakery": 4,
        "pizza": 4,
        "meal": 4,
    },
    "Travel": {
        "ticketing": 6,
        "railway": 6,
        "flight": 6,
        "airline": 6,
        "travel": 4,
        "bus": 4,
    },
    "Transport": {
        "cab": 5,
        "taxi": 5,
        "petrol": 6,
        "fuel": 6,
        "parking": 5,
        "metro": 5,
        "toll": 5,
        "fastag": 6,
    },
    "Bills & Utilities": {
        "electricity": 6,
        "broadband": 6,
        "recharge": 5,
        "water bill": 6,
        "gas bill": 6,
        "mobile bill": 6,
    },
    "Shopping": {
        "shopping": 5,
        "retail": 4,
        "mall": 4,
        "fashion": 4,
        "clothing": 4,
    },
    "Health": {
        "pharmacy": 6,
        "medical": 6,
        "medicine": 6,
        "hospital": 6,
        "clinic": 6,
        "diagnostic": 6,
    },
    "Subscriptions": {
        "subscription": 5,
        "membership": 5,
        "premium": 4,
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

    if normalized_alias in normalized_text:
        return True

    compact_alias = compact_text(normalized_alias)
    compact_description = compact_text(normalized_text)

    return len(compact_alias) >= 5 and compact_alias in compact_description


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
        text = text.replace(noise, " ")

    words = [
        word
        for word in text.split()
        if len(word) > 1
        and not any(character.isdigit() for character in word)
        and word not in {"com", "in"}
    ]

    return " ".join(words[:4]).title() or "Unknown"


def infer_category(narration):
    text = normalize_text(narration)
    scores = {}
    matched = {}

    for category_name, signals in CATEGORY_SIGNALS.items():
        score = 0
        terms = []

        for signal, weight in signals.items():
            if matches_alias(text, signal):
                score += weight
                terms.append(signal)

        if score:
            scores[category_name] = score
            matched[category_name] = terms

    if not scores:
        return (
            "Other",
            "Low",
            "No merchant or category signal identified",
        )

    best_category = max(scores, key=scores.get)
    confidence = "High" if scores[best_category] >= 6 else "Medium"
    terms = ", ".join(matched[best_category][:3])

    return (
        best_category,
        confidence,
        f"Matched: {terms}",
    )


def classify_transaction(row):
    narration = str(row["Narration"])
    withdrawal = float(row["Withdrawal"])
    deposit = float(row["Deposit"])

    profile = identify_known_merchant(narration)

    if profile:
        return (
            profile["merchant"],
            profile["category"],
            "High",
            f"Recognized merchant: {profile['merchant']}",
        )

    if deposit > 0 and withdrawal == 0:
        return (
            extract_unknown_merchant(narration),
            "Income",
            "Medium",
            "Deposit transaction",
        )

    category, confidence, reason = infer_category(narration)

    return (
        extract_unknown_merchant(narration),
        category,
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
        [data.reset_index(drop=True), classified],
        axis=1,
    )


def parse_amount(value):
    cleaned = re.sub(r"[^0-9.]", "", str(value))

    if not cleaned:
        return 0.0

    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def find_position(header, labels):
    header = header.lower()

    for label in labels:
        position = header.find(label.lower())
        if position >= 0:
            return position

    return -1


def get_field(line, start, end=None):
    line = line.ljust(180)
    return line[start:end].strip() if end else line[start:].strip()


def parse_txt(content):
    text = content.decode("utf-8-sig", errors="replace")
    lines = [line.expandtabs(8) for line in text.splitlines()]

    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "date" in line.lower()
            and "narration" in line.lower()
            and "closing balance" in line.lower()
        ),
        None,
    )

    if header_index is None:
        raise ValueError("Statement header was not found.")

    header = lines[header_index]
    positions = {
        "date": find_position(header, ["date"]),
        "narration": find_position(header, ["narration"]),
        "reference": find_position(
            header,
            ["chq./ref.no.", "chq./ref", "ref.no."],
        ),
        "value_date": find_position(
            header,
            ["value dt", "value date"],
        ),
        "withdrawal": find_position(
            header,
            ["withdrawal amt.", "withdrawal"],
        ),
        "deposit": find_position(
            header,
            ["deposit amt.", "deposit"],
        ),
        "balance": find_position(
            header,
            ["closing balance", "balance"],
        ),
    }

    if any(value < 0 for value in positions.values()):
        raise ValueError("One or more statement columns are missing.")

    transaction_start = re.compile(r"^\s*\d{2}/\d{2}/\d{2,4}\b")
    rows = []
    current = None

    for line in lines[header_index + 1:]:
        stripped = line.strip()

        if not stripped or set(stripped) <= {"-", " "}:
            continue

        if transaction_start.match(line):
            if current:
                rows.append(current)

            current = {
                "Date": get_field(
                    line,
                    positions["date"],
                    positions["narration"],
                ),
                "Narration": get_field(
                    line,
                    positions["narration"],
                    positions["reference"],
                ),
                "Withdrawal": get_field(
                    line,
                    positions["withdrawal"],
                    positions["deposit"],
                ),
                "Deposit": get_field(
                    line,
                    positions["deposit"],
                    positions["balance"],
                ),
                "Balance": get_field(line, positions["balance"]),
            }
        elif current:
            current["Narration"] += " " + stripped

    if current:
        rows.append(current)

    if not rows:
        raise ValueError("No transactions found.")

    data = pd.DataFrame(rows)
    data["Date"] = pd.to_datetime(
        data["Date"],
        format="%d/%m/%y",
        errors="coerce",
    )
    data["Withdrawal"] = data["Withdrawal"].map(parse_amount)
    data["Deposit"] = data["Deposit"].map(parse_amount)
    data["Balance"] = data["Balance"].map(parse_amount)

    data = data.dropna(subset=["Date"])
    data = data[
        (data["Withdrawal"] > 0)
        | (data["Deposit"] > 0)
    ].copy()

    data["Month"] = data["Date"].dt.to_period("M").astype(str)
    return classify_transactions(data)


def build_email_summary(data):
    spending = data[data["Withdrawal"] > 0]
    total_spending = spending["Withdrawal"].sum()
    total_income = data["Deposit"].sum()

    categories = (
        spending.groupby("Category")["Withdrawal"]
        .sum()
        .sort_values(ascending=False)
    )

    merchants = (
        spending.groupby("Merchant")["Withdrawal"]
        .sum()
        .sort_values(ascending=False)
        .head(10)
    )

    category_text = "\n".join(
        f"- {name}: ₹{value:,.2f}"
        for name, value in categories.items()
    )
    merchant_text = "\n".join(
        f"- {name}: ₹{value:,.2f}"
        for name, value in merchants.items()
    )

    return f"""Personal Bank Spending Summary

Period: {data['Date'].min():%d/%m/%Y} to {data['Date'].max():%d/%m/%Y}

Total spending: ₹{total_spending:,.2f}
Total income: ₹{total_income:,.2f}
Net cash flow: ₹{total_income - total_spending:,.2f}
Transactions: {len(data):,}

Spending by category:
{category_text or '- None'}

Top merchants:
{merchant_text or '- None'}

This is an aggregated summary only.
"""


def send_email(recipient, summary):
    config = st.secrets["gmail"]
    sender = config["sender_email"]
    password = config["app_password"].replace(" ", "")

    message = MIMEText(summary, "plain", "utf-8")
    message["Subject"] = "Personal Bank Spending Summary"
    message["From"] = sender
    message["To"] = recipient

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(sender, password)
        server.send_message(message)


def inr(value):
    return f"₹{value:,.2f}"


st.title("Personal Bank Spend Analyzer")
st.caption("Upload a fixed-width TXT statement for analysis.")

uploaded_file = st.file_uploader(
    "Upload your bank statement",
    type=["txt"],
)

if not uploaded_file:
    st.info("Upload a TXT statement to begin.")
    st.stop()

try:
    transactions = parse_txt(uploaded_file.getvalue())
except Exception as error:
    st.error(f"Could not read the statement: {error}")
    st.stop()

with st.sidebar:
    categories = sorted(transactions["Category"].unique())
    selected_categories = st.multiselect(
        "Categories",
        categories,
        default=categories,
    )

    start_date = transactions["Date"].min().date()
    end_date = transactions["Date"].max().date()
    date_range = st.date_input(
        "Date range",
        value=(start_date, end_date),
        min_value=start_date,
        max_value=end_date,
    )

if len(date_range) == 2:
    start_date, end_date = date_range

filtered = transactions[
    transactions["Category"].isin(selected_categories)
    & transactions["Date"].dt.date.between(start_date, end_date)
].copy()

spending = filtered["Withdrawal"].sum()
income = filtered["Deposit"].sum()

one, two, three = st.columns(3)
one.metric("Total spending", inr(spending))
two.metric("Total income", inr(income))
three.metric("Net cash flow", inr(income - spending))

overview, details, review = st.tabs(
    ["Overview", "Transactions", "Review"]
)

with overview:
    category_totals = (
        filtered[filtered["Withdrawal"] > 0]
        .groupby("Category")["Withdrawal"]
        .sum()
        .sort_values(ascending=False)
    )
    st.subheader("Spending by category")
    st.bar_chart(category_totals)

    st.subheader("Spending by merchant")
    merchant_totals = (
        filtered[filtered["Withdrawal"] > 0]
        .groupby(["Category", "Merchant"])["Withdrawal"]
        .sum()
        .reset_index()
        .sort_values("Withdrawal", ascending=False)
    )
    st.dataframe(
        merchant_totals,
        use_container_width=True,
        hide_index=True,
    )

with details:
    display = filtered.copy()
    display["Date"] = display["Date"].dt.strftime("%d/%m/%Y")
    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
    )

with review:
    low_confidence = filtered[
        filtered["Confidence"] == "Low"
    ]
    st.dataframe(
        low_confidence,
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.subheader("Email summary")

recipient = st.text_input(
    "Send summary to",
    value=st.secrets.get("gmail", {}).get("sender_email", ""),
)

if st.button("Send summary email"):
    try:
        send_email(
            recipient.strip(),
            build_email_summary(filtered),
        )
        st.success("Summary email sent.")
    except Exception as error:
        st.error(f"Email failed: {error}")
