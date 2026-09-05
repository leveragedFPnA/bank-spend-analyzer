import io
import re

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
        "aliases": [
            "dmart",
            "avenue supermarts",
        ],
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
    "Education": {
        "school": 6,
        "college": 6,
        "university": 6,
        "course": 5,
        "tuition": 6,
        "education": 5,
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


def contains_any(text, terms):
    return any(matches_alias(text, term) for term in terms)


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
        if not any(char.isdigit() for char in word)
        and len(word) > 1
    ]

    if not words:
        return "Unknown"

    return " ".join(words[:4]).title()


def infer_category_from_description(narration):
    text = normalize_text(narration)
    scores = {}
    matched_signals = {}

    for category_name, signals in CATEGORY_SIGNALS.items():
        category_score = 0
        category_matches = []

        for signal, weight in signals.items():
            if matches_alias(text, signal):
                category_score += weight
                category_matches.append(signal)

        if category_score:
            scores[category_name] = category_score
            matched_signals[category_name] = category_matches

    if not scores:
        return (
            "Other",
            "Low",
            "No merchant or category signal identified",
        )

    best_category = max(scores, key=scores.get)
    best_score = scores[best_category]
    matched = ", ".join(matched_signals[best_category][:3])

    confidence = "High" if best_score >= 6 else "Medium"

    return (
        best_category,
        confidence,
        f"Description matched: {matched}",
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
            f"Recognized merchant: "
            f"{known_merchant['merchant']}",
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


def parse_amount(value):
    text = str(value).strip()

    if not text or text in {"-", "—"}:
        return 0.0

    cleaned = re.sub(r"[^0-9.]", "", text)

    if not cleaned:
        return 0.0

    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def find_position(header, labels):
    header_lower = header.lower()

    for label in labels:
        position = header_lower.find(label.lower())

        if position >= 0:
            return position

    return -1


def get_field(line, start, end=None):
    padded_line = line.ljust(180)

    if end is None:
        return padded_line[start:].strip()

    return padded_line[start:end].strip()


def parse_txt(content):
    text = content.decode(
        "utf-8-sig",
        errors="replace",
    )

    lines = [
        line.expandtabs(8)
        for line in text.splitlines()
    ]

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
        raise ValueError(
            "Could not find the bank statement header."
        )

    header = lines[header_index]

    positions = {
        "date": find_position(header, ["date"]),
        "narration": find_position(header, ["narration"]),
        "reference": find_position(
            header,
            [
                "chq./ref.no.",
                "chq./ref",
                "ref.no.",
            ],
        ),
        "value_date": find_position(
            header,
            ["value dt", "value date"],
        ),
        "withdrawal": find_position(
            header,
            [
                "withdrawal amt.",
                "withdrawal",
            ],
        ),
        "deposit": find_position(
            header,
            [
                "deposit amt.",
                "deposit",
            ],
        ),
        "balance": find_position(
            header,
            [
                "closing balance",
                "balance",
            ],
        ),
    }

    missing = [
        name
        for name, position in positions.items()
        if position < 0
    ]

    if missing:
        raise ValueError(
            "Missing columns: " + ", ".join(missing)
        )

    transaction_start = re.compile(
        r"^\s*\d{2}/\d{2}/\d{2,4}\b"
    )

    rows = []
    current = None

    for line in lines[header_index + 1:]:
        stripped = line.strip()

        if not stripped:
            continue

        if set(stripped) <= {"-", " "}:
            continue

        if transaction_start.match(line):
            if current is not None:
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
                "Reference": get_field(
                    line,
                    positions["reference"],
                    positions["value_date"],
                ),
                "Value Date": get_field(
                    line,
                    positions["value_date"],
                    positions["withdrawal"],
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
                "Balance": get_field(
                    line,
                    positions["balance"],
                ),
            }
        elif current is not None:
            current["Narration"] = (
                current["Narration"] + " " + stripped
            ).strip()

    if current is not None:
        rows.append(current)

    if not rows:
        raise ValueError(
            "No transactions were found."
        )

    data = pd.DataFrame(rows)

    data["Date"] = pd.to_datetime(
        data["Date"],
        format="%d/%m/%y",
        errors="coerce",
    )

    data["Withdrawal"] = data["Withdrawal"].map(
        parse_amount
    )
    data["Deposit"] = data["Deposit"].map(
        parse_amount
    )
    data["Balance"] = data["Balance"].map(
        parse_amount
    )

    data = data.dropna(subset=["Date"]).copy()

    data = data[
        (data["Withdrawal"] > 0)
        | (data["Deposit"] > 0)
    ].copy()

    data["Type"] = data.apply(
        lambda row: (
            "Expense"
            if row["Withdrawal"] > 0
            else "Income"
        ),
        axis=1,
    )

    data["Month"] = data["Date"].dt.to_period("M").astype(str)
    data = classify_transactions(data)

    return data.sort_values("Date").reset_index(drop=True)


def format_inr(value):
    return f"₹{value:,.2f}"


st.title("Personal Bank Spend Analyzer")

st.caption(
    "Upload a fixed-width TXT bank statement. "
    "The file is processed for this session."
)

uploaded_file = st.file_uploader(
    "Upload your bank statement",
    type=["txt"],
)

if uploaded_file is None:
    st.info(
        "Upload a TXT statement exported from your bank."
    )
    st.stop()

try:
    transactions = parse_txt(
        uploaded_file.getvalue()
    )
except Exception as error:
    st.error(f"Could not read the statement: {error}")
    st.stop()

if transactions.empty:
    st.warning("No transactions were detected.")
    st.stop()

with st.sidebar:
    st.header("Filters")

    available_categories = sorted(
        transactions["Category"].unique()
    )

    selected_categories = st.multiselect(
        "Categories",
        available_categories,
        default=available_categories,
    )

    minimum_date = transactions["Date"].min().date()
    maximum_date = transactions["Date"].max().date()

    selected_dates = st.date_input(
        "Date range",
        value=(minimum_date, maximum_date),
        min_value=minimum_date,
        max_value=maximum_date,
    )

if isinstance(selected_dates, (tuple, list)):
    if len(selected_dates) == 2:
        start_date, end_date = selected_dates
    else:
        start_date = end_date = selected_dates[0]
else:
    start_date = end_date = selected_dates

filtered = transactions[
    transactions["Category"].isin(selected_categories)
    & transactions["Date"].dt.date.between(
        start_date,
        end_date,
    )
].copy()

if filtered.empty:
    st.warning(
        "No transactions match the selected filters."
    )
    st.stop()

total_spending = filtered["Withdrawal"].sum()
total_income = filtered["Deposit"].sum()
net_cash_flow = total_income - total_spending

one, two, three, four = st.columns(4)

one.metric(
    "Total spending",
    format_inr(total_spending),
)
two.metric(
    "Total income",
    format_inr(total_income),
)
three.metric(
    "Net cash flow",
    format_inr(net_cash_flow),
)
four.metric(
    "Transactions",
    f"{len(filtered):,}",
)

overview_tab, transactions_tab, review_tab = st.tabs(
    [
        "Overview",
        "Transactions",
        "Review",
    ]
)

with overview_tab:
    st.subheader("Spending by category")

    category_totals = (
        filtered[filtered["Withdrawal"] > 0]
        .groupby("Category")["Withdrawal"]
        .sum()
        .sort_values(ascending=False)
    )

    st.bar_chart(category_totals)

    st.subheader("Monthly spending")

    monthly_totals = (
        filtered[filtered["Withdrawal"] > 0]
        .groupby("Month")["Withdrawal"]
        .sum()
    )

    st.line_chart(monthly_totals)

    st.subheader("Spending by merchant")

    merchant_totals = (
        filtered[filtered["Withdrawal"] > 0]
        .groupby(
            ["Category", "Merchant"],
            as_index=False,
        )["Withdrawal"]
        .sum()
        .sort_values(
            "Withdrawal",
            ascending=False,
        )
    )

    st.dataframe(
        merchant_totals,
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Largest expenses")

    largest_expenses = (
        filtered[filtered["Withdrawal"] > 0]
        .sort_values(
            "Withdrawal",
            ascending=False,
        )
        .head(10)
    )

    st.dataframe(
        largest_expenses[
            [
                "Date",
                "Merchant",
                "Category",
                "Withdrawal",
                "Narration",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

with transactions_tab:
    display_data = filtered.copy()
    display_data["Date"] = display_data[
        "Date"
    ].dt.strftime("%d/%m/%Y")

    st.dataframe(
        display_data,
        use_container_width=True,
        hide_index=True,
    )

    csv_data = display_data.to_csv(
        index=False
    ).encode("utf-8")

    st.download_button(
        "Download analyzed statement",
        data=csv_data,
        file_name="spend_analysis.csv",
        mime="text/csv",
    )

with review_tab:
    st.subheader("Transactions requiring review")

    review_data = filtered[
        filtered["Confidence"] == "Low"
    ].copy()

    if review_data.empty:
        st.success(
            "No low-confidence transactions found."
        )
    else:
        st.dataframe(
            review_data[
                [
                    "Date",
                    "Narration",
                    "Merchant",
                    "Category",
                    "Confidence",
                    "Classification Reason",
                    "Withdrawal",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Other spending totals")

    other_totals = (
        filtered[
            (filtered["Category"] == "Other")
            & (filtered["Withdrawal"] > 0)
        ]
        .groupby("Merchant", as_index=False)["Withdrawal"]
        .sum()
        .sort_values(
            "Withdrawal",
            ascending=False,
        )
    )

    if other_totals.empty:
        st.info("There is no spending classified as Other.")
    else:
        st.dataframe(
            other_totals,
            use_container_width=True,
            hide_index=True,
        )

st.caption(
    "Merchant recognition and category inference use "
    "transparent rules. Unknown merchants remain Other."
)
