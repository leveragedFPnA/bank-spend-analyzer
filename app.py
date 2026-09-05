import hashlib
import hmac
import re
import smtplib
from email.mime.text import MIMEText

import pandas as pd
import streamlit as st
from supabase import create_client


st.set_page_config(
    page_title="Personal Spend Analyzer",
    page_icon="₹",
    layout="wide",
)


MERCHANT_PROFILES = [
    {"merchant": "IRCTC", "category": "Travel", "aliases": ["irctc", "indian railway", "railway ticket"]},
    {"merchant": "Uber", "category": "Transport", "aliases": ["uber", "uber india systems", "uberindia"]},
    {"merchant": "District Dining", "category": "Food & Dining", "aliases": ["district dining", "districtdining", "districtdining1"]},
    {"merchant": "Swiggy", "category": "Food & Dining", "aliases": ["swiggy"]},
    {"merchant": "Zomato", "category": "Food & Dining", "aliases": ["zomato"]},
    {"merchant": "Amazon", "category": "Shopping", "aliases": ["amazon", "amzn"]},
    {"merchant": "Flipkart", "category": "Shopping", "aliases": ["flipkart"]},
    {"merchant": "Myntra", "category": "Shopping", "aliases": ["myntra"]},
    {"merchant": "Airtel", "category": "Bills & Utilities", "aliases": ["airtel"]},
    {"merchant": "Jio", "category": "Bills & Utilities", "aliases": ["reliance jio", "jio"]},
    {"merchant": "Netflix", "category": "Subscriptions", "aliases": ["netflix"]},
    {"merchant": "Spotify", "category": "Subscriptions", "aliases": ["spotify"]},
    {"merchant": "Blinkit", "category": "Groceries", "aliases": ["blinkit"]},
    {"merchant": "Zepto", "category": "Groceries", "aliases": ["zepto"]},
    {"merchant": "DMart", "category": "Groceries", "aliases": ["dmart", "avenue supermarts"]},
]


CATEGORY_SIGNALS = {
    "Food & Dining": {"dining": 6, "restaurant": 6, "food": 5, "cafe": 5, "coffee": 4, "bakery": 4, "pizza": 4, "meal": 4},
    "Travel": {"ticketing": 6, "railway": 6, "flight": 6, "airline": 6, "travel": 4, "bus": 4},
    "Transport": {"cab": 5, "taxi": 5, "petrol": 6, "fuel": 6, "parking": 5, "metro": 5, "toll": 5, "fastag": 6},
    "Bills & Utilities": {"electricity": 6, "broadband": 6, "recharge": 5, "water bill": 6, "gas bill": 6, "mobile bill": 6},
    "Shopping": {"shopping": 5, "retail": 4, "mall": 4, "fashion": 4, "clothing": 4},
    "Health": {"pharmacy": 6, "medical": 6, "medicine": 6, "hospital": 6, "clinic": 6, "diagnostic": 6},
    "Subscriptions": {"subscription": 5, "membership": 5, "premium": 4},
    "Education": {"school": 6, "college": 6, "course": 5, "tuition": 6, "education": 5},
}


PROCESSING_NOISE = {
    "upi", "pos", "neft", "imps", "rtgs", "pinelabs", "pine labs",
    "razorpay", "payu", "payment", "payments", "india", "systems",
    "system", "ptsbi", "sbin",
}


ALL_CATEGORIES = [
    "Food & Dining",
    "Travel",
    "Transport",
    "Bills & Utilities",
    "Shopping",
    "Groceries",
    "Health",
    "Subscriptions",
    "Education",
    "Income",
    "Personal Transfer",
    "Cash Withdrawal",
    "Other",
]


def secret_section(name):
    try:
        return st.secrets[name]
    except KeyError:
        return {}


def require_app_password():
    settings = secret_section("app")
    required_password = settings.get("access_password")

    if not required_password:
        return

    if st.session_state.get("authenticated"):
        return

    st.title("Personal Spend Analyzer")
    entered_password = st.text_input(
        "Enter your private app password",
        type="password",
    )

    if st.button("Unlock"):
        if hmac.compare_digest(
            entered_password,
            required_password,
        ):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    st.stop()


require_app_password()


@st.cache_resource
def create_database_client(url, key):
    return create_client(url, key)


def get_database():
    settings = secret_section("supabase")

    if not settings:
        return None

    url = str(settings.get("url", "")).rstrip("/")
    key = str(settings.get("key", ""))

    if not url or not key:
        return None

    return create_database_client(url, key)


def normalize_text(value):
    text = str(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
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

    return (
        len(compact_alias) >= 5
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
            rf"\b{re.escape(noise)}\b",
            " ",
            text,
        )

    words = [
        word
        for word in text.split()
        if len(word) > 1
        and not any(character.isdigit() for character in word)
        and word not in {"com", "in"},
    ]

    return " ".join(words[:4]).title() or "Unknown"


def infer_category(narration):
    text = normalize_text(narration)
    scores = {}
    matched_terms = {}

    for category_name, signals in CATEGORY_SIGNALS.items():
        score = 0
        terms = []

        for signal, weight in signals.items():
            if matches_alias(text, signal):
                score += weight
                terms.append(signal)

        if score:
            scores[category_name] = score
            matched_terms[category_name] = terms

    if not scores:
        return (
            "Other",
            "Low",
            "No merchant or category signal identified",
        )

    best_category = max(scores, key=scores.get)
    best_score = scores[best_category]
    confidence = "High" if best_score >= 6 else "Medium"
    terms = ", ".join(matched_terms[best_category][:3])

    return (
        best_category,
        confidence,
        f"Matched: {terms}",
    )


def classify_transaction(row):
    narration = str(row["Narration"])
    withdrawal = float(row["Withdrawal"])
    deposit = float(row["Deposit"])

    known_merchant = identify_known_merchant(narration)

    if known_merchant:
        return (
            known_merchant["merchant"],
            known_merchant["category"],
            "High",
            f"Recognized merchant: {known_merchant['merchant']}",
        )

    if deposit > 0 and withdrawal == 0:
        return (
            extract_unknown_merchant(narration),
            "Income",
            "Medium",
            "Deposit transaction",
        )

    category, confidence, reason = infer_category(narration)

    if category == "Other":
        is_upi = "upi" in normalize_text(narration)
        has_person_signal = bool(
            re.search(r"\d{10}", narration)
            or "@" in narration
        )

        if is_upi and has_person_signal:
            category = "Personal Transfer"
            confidence = "Medium"
            reason = "Person-to-person UPI pattern detected"

    return (
        extract_unknown_merchant(narration),
        category,
        confidence,
        reason,
    )


def make_transaction_key(row):
    value = "|".join(
        [
            str(row["Date"].date()),
            normalize_text(row["Narration"]),
            f"{float(row['Withdrawal']):.2f}",
            f"{float(row['Deposit']):.2f}",
            f"{float(row['Balance']):.2f}",
        ]
    )

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def classify_transactions(data):
    classified = data.apply(
        classify_transaction,
        axis=1,
        result_type="expand",
    )

    classified.columns = [
        "Merchant",
        "Predicted Category",
        "Confidence",
        "Classification Reason",
    ]

    result = pd.concat(
        [
            data.reset_index(drop=True),
            classified.reset_index(drop=True),
        ],
        axis=1,
    )

    result["Final Category"] = result["Predicted Category"]
    result["Category"] = result["Final Category"]
    result["Transaction Key"] = result.apply(
        make_transaction_key,
        axis=1,
    )

    return result


def apply_saved_learning(data, database):
    if database is None:
        return data

    try:
        saved_rows = (
            database.table("transactions")
            .select(
                "transaction_key,merchant,final_category"
            )
            .execute()
            .data
            or []
        )

        rules = (
            database.table("category_rules")
            .select("pattern,merchant,category")
            .execute()
            .data
            or []
        )
    except Exception as error:
        st.warning(
            f"Saved learning could not be loaded: {error}"
        )
        return data

    saved_map = {
        row["transaction_key"]: row
        for row in saved_rows
        if row.get("transaction_key")
    }

    rules = sorted(
        rules,
        key=lambda row: len(
            normalize_text(row.get("pattern", ""))
        ),
        reverse=True,
    )

    result = data.copy()

    for index, row in result.iterrows():
        narration = normalize_text(row["Narration"])
        merchant = normalize_text(row["Merchant"])

        for rule in rules:
            pattern = normalize_text(rule.get("pattern", ""))

            if pattern and (
                pattern in narration
                or pattern in merchant
            ):
                category = rule.get("category", "Other")
                result.at[index, "Predicted Category"] = category
                result.at[index, "Final Category"] = category
                result.at[index, "Category"] = category
                result.at[index, "Confidence"] = "High"
                result.at[index, "Classification Reason"] = (
                    "Matched your saved personal rule"
                )
                break

    for index, row in result.iterrows():
        saved = saved_map.get(row["Transaction Key"])

        if saved and saved.get("final_category"):
            result.at[index, "Final Category"] = (
                saved["final_category"]
            )
            result.at[index, "Category"] = (
                saved["final_category"]
            )

            if saved.get("merchant"):
                result.at[index, "Merchant"] = saved["merchant"]

    return result


def parse_amount(value):
    cleaned = re.sub(r"[^0-9.]", "", str(value))

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
    line = line.ljust(180)

    if end is None:
        return line[start:].strip()

    return line[start:end].strip()


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

    if any(
        position < 0
        for position in positions.values()
    ):
        raise ValueError(
            "One or more statement columns are missing."
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
        raise ValueError("No transactions found.")

    data = pd.DataFrame(rows)

    data["Date"] = pd.to_datetime(
        data["Date"],
        dayfirst=True,
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

    data["Month"] = data["Date"].dt.to_period("M").astype(str)

    return classify_transactions(data)


def save_transactions(database, data):
    records = []

    for _, row in data.iterrows():
        records.append(
            {
                "transaction_key": row["Transaction Key"],
                "transaction_date": row["Date"].date().isoformat(),
                "narration": row["Narration"],
                "merchant": row["Merchant"],
                "withdrawal": float(row["Withdrawal"]),
                "deposit": float(row["Deposit"]),
                "balance": float(row["Balance"]),
                "predicted_category": row["Predicted Category"],
                "final_category": row["Final Category"],
                "confidence": row["Confidence"],
                "classification_reason": row[
                    "Classification Reason"
                ],
            }
        )

    database.table("transactions").upsert(
        records,
        on_conflict="transaction_key",
    ).execute()


def save_category_rules(database, data):
    rules = []

    for _, row in data.iterrows():
        merchant = normalize_text(row["Merchant"])
        category = row["Final Category"]

        if merchant in {"", "unknown"} or len(merchant) < 4:
            continue

        rules.append(
            {
                "pattern": merchant,
                "merchant": row["Merchant"],
                "category": category,
            }
        )

    if rules:
        database.table("category_rules").upsert(
            rules,
            on_conflict="pattern",
        ).execute()


def load_history(database):
    rows = (
        database.table("transactions")
        .select("*")
        .order("transaction_date")
        .execute()
        .data
        or []
    )

    if not rows:
        return pd.DataFrame()

    history = pd.DataFrame(rows)
    history["Date"] = pd.to_datetime(
        history["transaction_date"]
    )
    history["Withdrawal"] = pd.to_numeric(
        history["withdrawal"],
        errors="coerce",
    ).fillna(0)
    history["Deposit"] = pd.to_numeric(
        history["deposit"],
        errors="coerce",
    ).fillna(0)
    history["Category"] = history[
        "final_category"
    ].fillna(history["predicted_category"])
    history["Month"] = history[
        "Date"
    ].dt.to_period("M").astype(str)

    return history


def forecast_next_month(history):
    spending = history[
        history["Withdrawal"] > 0
    ].copy()

    if spending.empty:
        return pd.DataFrame()

    monthly = (
        spending.groupby(
            ["Month", "Category"]
        )["Withdrawal"]
        .sum()
        .unstack(fill_value=0)
        .sort_index()
    )

    forecast_rows = []

    for category in monthly.columns:
        values = monthly[category].tail(3).tolist()

        if not values:
            continue

        weights = list(range(1, len(values) + 1))
        estimate = sum(
            value * weight
            for value, weight in zip(values, weights)
        ) / sum(weights)

        forecast_rows.append(
            {
                "Category": category,
                "Forecast": round(estimate, 2),
                "Months used": len(values),
            }
        )

    return pd.DataFrame(forecast_rows).sort_values(
        "Forecast",
        ascending=False,
    )


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

Period: {data["Date"].min():%d/%m/%Y} to {data["Date"].max():%d/%m/%Y}

Total spending: ₹{total_spending:,.2f}
Total income: ₹{total_income:,.2f}
Net cash flow: ₹{total_income - total_spending:,.2f}
Transactions: {len(data):,}

Spending by category:
{category_text or "- None"}

Top merchants:
{merchant_text or "- None"}

This is an aggregated summary only.
"""


def send_email(recipient, summary):
    settings = secret_section("gmail")

    if not settings:
        raise ValueError(
            "The [gmail] secret section is missing."
        )

    sender = settings["sender_email"]
    password = settings["app_password"].replace(" ", "")

    message = MIMEText(summary, "plain", "utf-8")
    message["Subject"] = "Personal Bank Spending Summary"
    message["From"] = sender
    message["To"] = recipient

    with smtplib.SMTP(
        "smtp.gmail.com",
        587,
        timeout=30,
    ) as server:
        server.starttls()
        server.login(sender, password)
        server.send_message(message)


def inr(value):
    return f"₹{value:,.2f}"


st.title("Personal Bank Spend Analyzer")
st.caption(
    "Upload a fixed-width TXT bank statement."
)

database = get_database()

uploaded_file = st.file_uploader(
    "Upload your bank statement",
    type=["txt"],
)

if not uploaded_file:
    st.info("Upload a TXT statement to begin.")
    st.stop()

try:
    transactions = parse_txt(
        uploaded_file.getvalue()
    )
    transactions = apply_saved_learning(
        transactions,
        database,
    )
except Exception as error:
    st.error(f"Could not read the statement: {error}")
    st.stop()

if database is not None:
    if st.button("Save statement to online history"):
        try:
            save_transactions(database, transactions)
            st.success(
                "Statement saved to Supabase history."
            )
        except Exception as error:
            st.error(
                f"Could not save statement: {error}"
            )
else:
    st.warning(
        "Supabase is not connected. Add the [supabase] "
        "section to Streamlit Secrets."
    )

with st.sidebar:
    st.header("Filters")

    if database is not None:
        if st.button("Test Supabase connection"):
            try:
                database.table("transactions").select(
                    "id"
                ).limit(1).execute()
                st.success("Supabase connection works.")
            except Exception as error:
                st.error(f"Connection failed: {error}")

    categories = sorted(
        transactions["Category"].unique()
    )

    selected_categories = st.multiselect(
        "Categories",
        categories,
        default=categories,
    )

    minimum_date = transactions["Date"].min().date()
    maximum_date = transactions["Date"].max().date()

    date_range = st.date_input(
        "Date range",
        value=(minimum_date, maximum_date),
        min_value=minimum_date,
        max_value=maximum_date,
    )

if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date = minimum_date
    end_date = maximum_date

filtered = transactions[
    transactions["Category"].isin(selected_categories)
    & transactions["Date"].dt.date.between(
        start_date,
        end_date,
    )
].copy()

if filtered.empty:
    st.warning("No transactions match the filters.")
    st.stop()

spending = filtered["Withdrawal"].sum()
income = filtered["Deposit"].sum()

one, two, three = st.columns(3)
one.metric("Total spending", inr(spending))
two.metric("Total income", inr(income))
three.metric("Net cash flow", inr(income - spending))

overview, details, review, history_tab = st.tabs(
    [
        "Overview",
        "Transactions",
        "Review Other",
        "History & Forecast",
    ]
)

with overview:
    st.subheader("Spending by category")

    category_totals = (
        filtered[filtered["Withdrawal"] > 0]
        .groupby("Category")["Withdrawal"]
        .sum()
        .sort_values(ascending=False)
    )

    st.bar_chart(category_totals)

    st.subheader("Spending by merchant")

    merchant_totals = (
        filtered[filtered["Withdrawal"] > 0]
        .groupby(
            ["Category", "Merchant"]
        )["Withdrawal"]
        .sum()
        .reset_index()
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

with details:
    display = filtered.copy()
    display["Date"] = display["Date"].dt.strftime(
        "%d/%m/%Y"
    )

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
    )

with review:
    st.subheader("Allocate Other expenses")

    review_data = filtered[
        (filtered["Category"] == "Other")
        | (filtered["Confidence"] == "Low")
    ].copy()

    if review_data.empty:
        st.success(
            "There are no low-confidence or Other expenses."
        )
    else:
        review_columns = [
            "Transaction Key",
            "Date",
            "Narration",
            "Merchant",
            "Predicted Category",
            "Final Category",
            "Withdrawal",
        ]

        edited = st.data_editor(
            review_data[review_columns],
            column_config={
                "Final Category": st.column_config.SelectboxColumn(
                    "Allocate to category",
                    options=ALL_CATEGORIES,
                    required=True,
                ),
                "Transaction Key": None,
            },
            disabled=[
                "Transaction Key",
                "Date",
                "Narration",
                "Merchant",
                "Predicted Category",
                "Withdrawal",
            ],
            hide_index=True,
            use_container_width=True,
            key="category_editor",
        )

        if st.button("Save category allocations"):
            if database is None:
                st.error(
                    "Connect Supabase before saving allocations."
                )
            else:
                try:
                    changes = transactions.copy()

                    for _, edited_row in edited.iterrows():
                        key = edited_row["Transaction Key"]
                        new_category = edited_row[
                            "Final Category"
                        ]

                        mask = (
                            changes["Transaction Key"] == key
                        )

                        changes.loc[
                            mask,
                            "Final Category",
                        ] = new_category
                        changes.loc[
                            mask,
                            "Category",
                        ] = new_category

                    save_transactions(
                        database,
                        changes,
                    )
                    save_category_rules(
                        database,
                        changes[
                            changes["Transaction Key"].isin(
                                edited["Transaction Key"]
                            )
                        ],
                    )

                    st.success(
                        "Your allocations were saved."
                    )
                    st.rerun()
                except Exception as error:
                    st.error(
                        f"Could not save allocations: {error}"
                    )

with history_tab:
    if database is None:
        st.info(
            "Connect Supabase to view your history."
        )
    else:
        try:
            history = load_history(database)

            if history.empty:
                st.info(
                    "Save a statement to build history."
                )
            else:
                st.subheader("Historical monthly spending")

                monthly_history = (
                    history[history["Withdrawal"] > 0]
                    .groupby("Month")["Withdrawal"]
                    .sum()
                )

                st.line_chart(monthly_history)

                st.subheader(
                    "Estimated next-month spending"
                )

                forecast = forecast_next_month(history)

                if forecast.empty:
                    st.info(
                        "Not enough expense history yet."
                    )
                else:
                    st.dataframe(
                        forecast,
                        use_container_width=True,
                        hide_index=True,
                    )

                    st.metric(
                        "Estimated total next-month spending",
                        inr(forecast["Forecast"].sum()),
                    )

                    st.caption(
                        "This is a weighted estimate based on "
                        "your latest saved months, not a guarantee."
                    )
        except Exception as error:
            st.error(
                f"Could not load history: {error}"
            )

st.divider()
st.subheader("Email summary")

gmail_settings = secret_section("gmail")
default_recipient = gmail_settings.get(
    "sender_email",
    "",
)

recipient = st.text_input(
    "Send summary to",
    value=default_recipient,
)

if st.button("Send summary email"):
    if not recipient.strip():
        st.warning("Enter a recipient email address.")
    else:
        try:
            send_email(
                recipient.strip(),
                build_email_summary(filtered),
            )
            st.success("Summary email sent.")
        except Exception as error:
            st.error(f"Email failed: {error}")
