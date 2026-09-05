import hashlib
import hmac
import io
import re
import smtplib
from email.mime.text import MIMEText

import pandas as pd
import streamlit as st

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    from supabase import create_client
except ImportError:
    create_client = None


st.set_page_config(
    page_title="Bank Spend Analyzer",
    page_icon="💰",
    layout="wide",
)

CATEGORIES = [
    "Income",
    "Housing",
    "Utilities",
    "Groceries",
    "Food & Dining",
    "Transportation",
    "Fuel",
    "Shopping",
    "Entertainment",
    "Healthcare",
    "Debt Payments",
    "Investments",
    "Transfers",
    "Bank Fees",
    "Taxes",
    "Other",
]


def secret_section(name):
    try:
        return st.secrets.get(name, {})
    except Exception:
        return {}


def clean(value):
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def require_password():
    expected = secret_section("app").get("access_password")

    if not expected or st.session_state.get("authenticated"):
        return

    st.title("Bank Spend Analyzer")
    entered = st.text_input("App password", type="password")

    if st.button("Unlock", type="primary"):
        if hmac.compare_digest(str(entered), str(expected)):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    st.stop()


def get_database():
    if create_client is None:
        return None, "Add supabase to requirements.txt."

    settings = secret_section("supabase")
    url = settings.get("url")
    key = settings.get("key")

    if not url or not key:
        return None, "Supabase URL or key is missing from Secrets."

    try:
        return create_client(url, key), None
    except Exception as error:
        return None, str(error)


def find_column(dataframe, keywords):
    for column in dataframe.columns:
        column_name = clean(column).replace("_", " ")
        if any(keyword in column_name for keyword in keywords):
            return column
    return None


def parse_amount(value):
    if pd.isna(value):
        return None

    text = str(value).strip()

    if not text:
        return None

    negative_parentheses = text.startswith("(") and text.endswith(")")
    text = text.replace("$", "").replace(",", "")
    text = text.replace("(", "").replace(")", "")
    text = re.sub(r"[^0-9.\-]", "", text)

    number = pd.to_numeric(text, errors="coerce")

    if pd.isna(number):
        return None

    number = float(number)

    if negative_parentheses:
        number = -abs(number)

    return number


def merchant_from_description(description):
    words = re.findall(r"[a-z0-9]+", clean(description))
    words = [word for word in words if len(word) > 2]
    return " ".join(words[:5]) or "Unknown merchant"


def classify_transaction(description, transaction_type):
    text = clean(description)
    transaction_type = clean(transaction_type)

    investment_terms = [
        "fidelity",
        "vanguard",
        "schwab",
        "robinhood",
        "etrade",
        "wealthfront",
        "betterment",
        "brokerage",
        "stock",
        "invest",
        "crypto",
        "coinbase",
    ]

    if any(term in text for term in investment_terms):
        return "Investments"

    if any(term in text for term in ["payroll", "salary", "direct deposit"]):
        return "Income"

    if transaction_type == "credit" and any(
        term in text for term in ["deposit", "refund", "interest", "dividend"]
    ):
        return "Income"

    category_terms = {
        "Housing": ["rent", "mortgage", "hoa", "property management"],
        "Utilities": ["electric", "water", "internet", "verizon", "t-mobile"],
        "Groceries": ["grocery", "aldi", "kroger", "whole foods", "market"],
        "Food & Dining": [
            "restaurant",
            "cafe",
            "coffee",
            "doordash",
            "ubereats",
        ],
        "Transportation": ["uber", "lyft", "transit", "parking", "toll"],
        "Fuel": ["shell", "chevron", "exxon", "bp", "fuel", "gas station"],
        "Shopping": ["amazon", "target", "costco", "walmart", "store"],
        "Entertainment": ["netflix", "spotify", "movie", "theater", "hulu"],
        "Healthcare": ["medical", "pharmacy", "doctor", "dental", "health"],
        "Debt Payments": ["loan", "credit card payment"],
        "Transfers": ["transfer", "zelle", "venmo", "cash app"],
        "Bank Fees": ["fee", "service charge", "overdraft"],
        "Taxes": ["irs", "tax", "state tax"],
    }

    for category, terms in category_terms.items():
        if any(term in text for term in terms):
            return category

    return "Other"


def read_delimited_file(uploaded_file):
    raw_bytes = uploaded_file.getvalue()
    encodings = ["utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"]
    delimiters = [",", "\t", ";", "|"]
    fallback = None

    for encoding in encodings:
        try:
            text = raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue

        if not text.strip():
            continue

        lines = text.splitlines()
        max_skip = min(40, max(1, len(lines) - 1))

        for skip_rows in range(max_skip):
            for delimiter in delimiters:
                try:
                    dataframe = pd.read_csv(
                        io.StringIO(text),
                        sep=delimiter,
                        skiprows=skip_rows,
                        dtype=str,
                        engine="python",
                        on_bad_lines="skip",
                    ).dropna(how="all")

                    if dataframe.empty or len(dataframe.columns) < 2:
                        continue

                    headers = " ".join(
                        clean(column) for column in dataframe.columns
                    )

                    expected_headers = [
                        "date",
                        "description",
                        "amount",
                        "debit",
                        "credit",
                        "merchant",
                        "memo",
                        "transaction",
                    ]

                    if any(header in headers for header in expected_headers):
                        return dataframe

                    if fallback is None:
                        fallback = dataframe

                except Exception:
                    continue

    if fallback is not None:
        return fallback

    raise ValueError(
        "The file could not be read. Export it from your bank as CSV or Excel."
    )


def standardize_dataframe(raw_data, source_file):
    data = raw_data.dropna(how="all").copy()

    if data.empty:
        raise ValueError("The file contains no usable rows.")

    data.columns = [str(column).strip() for column in data.columns]

    date_column = find_column(
        data,
        ["date", "posted", "transaction date", "trans date"],
    )

    description_column = find_column(
        data,
        ["description", "memo", "details", "merchant", "payee", "name"],
    )

    amount_column = find_column(
        data,
        ["amount", "transaction amount"],
    )

    debit_column = find_column(
        data,
        ["debit", "withdrawal", "charge"],
    )

    credit_column = find_column(
        data,
        ["credit", "deposit"],
    )

    if date_column is None:
        raise ValueError("Could not find a date column.")

    result = pd.DataFrame()

    result["transaction_date"] = pd.to_datetime(
        data[date_column],
        errors="coerce",
    ).dt.date

    if description_column:
        result["description"] = (
            data[description_column].fillna("").astype(str)
        )
    else:
        text_columns = data.select_dtypes(include=["object"]).columns
        result["description"] = (
            data[text_columns]
            .fillna("")
            .astype(str)
            .agg(" ".join, axis=1)
        )

    if debit_column or credit_column:
        debit = (
            data[debit_column].apply(parse_amount).fillna(0)
            if debit_column
            else pd.Series(0.0, index=data.index)
        )

        credit = (
            data[credit_column].apply(parse_amount).fillna(0)
            if credit_column
            else pd.Series(0.0, index=data.index)
        )

        result["amount"] = debit.abs().where(
            debit.abs() > 0,
            credit.abs(),
        )

        result["transaction_type"] = debit.abs().where(
            debit.abs() > 0,
            credit.abs(),
        ).apply(
            lambda value: "Debit" if value > 0 else "Credit"
        )

    elif amount_column:
        amounts = data[amount_column].apply(parse_amount)

        result["amount"] = amounts.abs()
        result["transaction_type"] = amounts.apply(
            lambda value: (
                "Debit"
                if pd.notna(value) and value < 0
                else "Credit"
            )
        )

    else:
        raise ValueError("Could not find an amount, debit, or credit column.")

    result["merchant"] = result["description"].apply(
        merchant_from_description
    )

    result["category"] = result.apply(
        lambda row: classify_transaction(
            row["description"],
            row["transaction_type"],
        ),
        axis=1,
    )

    result["source_file"] = source_file
    result["user_id"] = "personal"

    result = result.dropna(
        subset=["transaction_date", "amount"]
    )

    result["amount"] = pd.to_numeric(
        result["amount"],
        errors="coerce",
    )

    result = result.dropna(subset=["amount"])

    if result.empty:
        raise ValueError(
            "No valid transactions were found. Check the date and amount columns."
        )

    base_keys = result.apply(
        lambda row: "|".join(
            [
                str(row["transaction_date"]),
                clean(row["description"]),
                f"{float(row['amount']):.2f}",
                clean(row["transaction_type"]),
            ]
        ),
        axis=1,
    )

    occurrence = base_keys.groupby(base_keys).cumcount()

    result["transaction_key"] = [
        hashlib.sha256(
            f"{base}|{number}".encode("utf-8")
        ).hexdigest()
        for base, number in zip(base_keys, occurrence)
    ]

    return result[
        [
            "transaction_key",
            "user_id",
            "transaction_date",
            "description",
            "merchant",
            "amount",
            "transaction_type",
            "category",
            "source_file",
        ]
    ].drop_duplicates("transaction_key")


def parse_pdf(uploaded_file):
    if PdfReader is None:
        raise ValueError("Add pypdf to requirements.txt.")

    reader = PdfReader(uploaded_file)
    rows = []

    date_pattern = r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
    amount_pattern = r"-?\(?\$?\d[\d,]*\.\d{2}\)?"

    for page in reader.pages:
        text = page.extract_text() or ""

        for line in text.splitlines():
            date_match = re.search(date_pattern, line)
            amounts = re.findall(amount_pattern, line)

            if not date_match or not amounts:
                continue

            date_text = date_match.group(0)
            amount_text = amounts[-1]
            description = line.replace(date_text, "", 1)
            description = description.replace(amount_text, "", 1)

            rows.append(
                {
                    "Date": date_text,
                    "Description": description.strip(" -|"),
                    "Amount": parse_amount(amount_text),
                }
            )

    if not rows:
        raise ValueError("No transactions were found in this PDF.")

    return pd.DataFrame(rows)


def parse_file(uploaded_file):
    filename = uploaded_file.name.lower()

    if filename.endswith((".csv", ".txt")):
        raw_data = read_delimited_file(uploaded_file)
        return standardize_dataframe(raw_data, uploaded_file.name)

    if filename.endswith((".xlsx", ".xls")):
        raw_data = pd.read_excel(uploaded_file)
        return standardize_dataframe(raw_data, uploaded_file.name)

    if filename.endswith(".pdf"):
        raw_data = parse_pdf(uploaded_file)
        return standardize_dataframe(raw_data, uploaded_file.name)

    raise ValueError("Use CSV, TXT, XLSX, XLS, or PDF.")


def load_transactions(database):
    response = (
        database.table("transactions")
        .select("*")
        .eq("user_id", "personal")
        .order("transaction_date", desc=True)
        .execute()
    )

    data = pd.DataFrame(response.data or [])

    if data.empty:
        return data

    data["transaction_date"] = pd.to_datetime(
        data["transaction_date"],
        errors="coerce",
    ).dt.date

    data["amount"] = pd.to_numeric(
        data["amount"],
        errors="coerce",
    )

    data["category"] = data["category"].fillna("Other")
    data["merchant"] = data["merchant"].fillna("")
    data["transaction_type"] = data["transaction_type"].fillna("Debit")

    return data


def load_rules(database):
    response = (
        database.table("category_rules")
        .select("*")
        .eq("user_id", "personal")
        .execute()
    )

    rules = {}

    for row in response.data or []:
        merchant = row.get("merchant") or row.get("pattern")
        category = row.get("final_category") or row.get("category")

        if merchant and category:
            rules[clean(merchant)] = category

    return rules


def apply_rules(data, rules):
    result = data.copy()

    for index, row in result.iterrows():
        merchant = clean(row.get("merchant", ""))

        if merchant in rules:
            result.at[index, "category"] = rules[merchant]

    return result


def save_transactions(database, data):
    if data is None or data.empty:
        raise ValueError(
            "No usable transactions were found in the uploaded file."
        )

    records = []

    for row in data.to_dict("records"):
        records.append(
            {
                "transaction_key": str(row["transaction_key"]),
                "user_id": "personal",
                "transaction_date": str(row["transaction_date"]),
                "description": str(row["description"]),
                "merchant": str(row["merchant"]),
                "amount": float(row["amount"]),
                "transaction_type": str(row["transaction_type"]),
                "category": str(row["category"]),
                "source_file": str(row["source_file"]),
            }
        )

    if not records:
        raise ValueError("No transaction records were created.")

    for start in range(0, len(records), 500):
        batch = records[start : start + 500]

        database.table("transactions").upsert(
            batch,
            on_conflict="transaction_key",
            returning="minimal",
        ).execute()


def save_category_changes(database, original, edited):
    original_categories = original.set_index(
        "transaction_key"
    )["category"].to_dict()

    changed = 0
    rules = {}

    for _, row in edited.iterrows():
        key = str(row["transaction_key"]).strip()
        new_category = str(row["category"]).strip() or "Other"
        old_category = str(original_categories.get(key, "Other"))

        if not key or new_category == old_category:
            continue

        database.table("transactions").update(
            {"category": new_category},
            returning="minimal",
        ).eq(
            "transaction_key",
            key,
        ).execute()

        merchant = str(row["merchant"]).strip()

        if merchant:
            rules[clean(merchant)] = {
                "user_id": "personal",
                "merchant": merchant,
                "final_category": new_category,
                "pattern": merchant,
                "category": new_category,
            }

        changed += 1

    for rule in rules.values():
        database.table("category_rules").upsert(
            rule,
            on_conflict="user_id,merchant",
            returning="minimal",
        ).execute()

    return changed


def send_email(summary, recipient):
    settings = secret_section("gmail")
    sender = settings.get("address")
    app_password = settings.get("app_password")

    if not sender or not app_password:
        raise ValueError(
            "Add gmail.address and gmail.app_password to Secrets."
        )

    message = MIMEText(summary)
    message["Subject"] = "Bank Spend Analyzer Summary"
    message["From"] = sender
    message["To"] = recipient

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender, app_password)
        server.send_message(message)


def show_forecast(data):
    expenses = data[
        data["transaction_type"].str.lower() == "debit"
    ].copy()

    if expenses.empty:
        st.info("No debit transactions are available.")
        return

    expenses["month"] = pd.to_datetime(
        expenses["transaction_date"]
    ).dt.to_period("M").astype(str)

    monthly = expenses.groupby("month")["amount"].sum()
    average = monthly.tail(3).mean()

    st.subheader("Spending forecast")

    first, second = st.columns(2)
    first.metric("Average monthly spending", f"${average:,.2f}")
    second.metric("Next-month estimate", f"${average:,.2f}")

    st.line_chart(monthly)


require_password()

st.title("Bank Spend Analyzer")
st.caption("Upload, review, and categorize every transaction.")

database, database_error = get_database()

if database_error:
    st.sidebar.warning(database_error)

if "data" not in st.session_state:
    if database is not None:
        try:
            st.session_state.data = load_transactions(database)
        except Exception as error:
            st.session_state.data = pd.DataFrame()
            st.error(f"Saved transactions could not be loaded: {error}")
    else:
        st.session_state.data = pd.DataFrame()

with st.sidebar:
    st.header("Import statement")

    uploaded_file = st.file_uploader(
        "Upload CSV, Excel, PDF, or TXT",
        type=["csv", "xlsx", "xls", "pdf", "txt"],
    )

    if st.button("Import statement", type="primary"):
        if uploaded_file is None:
            st.warning("Choose a file first.")
        else:
            try:
                imported = parse_file(uploaded_file)

                if imported.empty:
                    raise ValueError(
                        "No transactions were found in the file."
                    )

                if database is None:
                    raise ValueError(
                        "Supabase is required to save transactions."
                    )

                imported = apply_rules(
                    imported,
                    load_rules(database),
                )

                save_transactions(database, imported)
                st.session_state.data = load_transactions(database)

                st.success(
                    f"Imported {len(imported)} transaction(s)."
                )
                st.rerun()

            except Exception as error:
                st.exception(error)

data = st.session_state.data

if data.empty:
    st.info("Upload a statement to begin.")
    st.stop()

data["transaction_date"] = pd.to_datetime(
    data["transaction_date"],
    errors="coerce",
).dt.date

data["category"] = data["category"].fillna("Other")
data["merchant"] = data["merchant"].fillna("")
data["transaction_type"] = data["transaction_type"].fillna("Debit")

min_date = min(data["transaction_date"])
max_date = max(data["transaction_date"])

with st.sidebar:
    selected_dates = st.date_input(
        "Date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

if isinstance(selected_dates, (tuple, list)) and len(selected_dates) == 2:
    start_date, end_date = selected_dates
else:
    start_date = end_date = selected_dates

filtered = data[
    data["transaction_date"].between(start_date, end_date)
].copy()

if filtered.empty:
    st.warning("No transactions match the selected dates.")
    st.stop()

debits = filtered[
    filtered["transaction_type"].str.lower() == "debit"
]["amount"].sum()

credits = filtered[
    filtered["transaction_type"].str.lower() == "credit"
]["amount"].sum()

first, second, third, fourth = st.columns(4)
first.metric("Transactions", f"{len(filtered):,}")
second.metric("Spending", f"${debits:,.2f}")
third.metric("Income", f"${credits:,.2f}")
fourth.metric("Net", f"${credits - debits:,.2f}")

st.subheader("Review and categorize all transactions")
st.caption("Edit any category, then click Save category changes.")

review_columns = [
    "transaction_key",
    "transaction_date",
    "description",
    "merchant",
    "amount",
    "transaction_type",
    "category",
]

review_data = filtered[review_columns].copy()

edited_data = st.data_editor(
    review_data,
    hide_index=True,
    use_container_width=True,
    num_rows="fixed",
    key="transaction_editor",
    column_config={
        "transaction_key": None,
        "transaction_date": st.column_config.DateColumn("Date"),
        "description": st.column_config.TextColumn("Description"),
        "merchant": st.column_config.TextColumn("Merchant"),
        "amount": st.column_config.NumberColumn(
            "Amount",
            format="$%.2f",
        ),
        "transaction_type": st.column_config.TextColumn("Type"),
        "category": st.column_config.SelectboxColumn(
            "Category",
            options=CATEGORIES,
            required=True,
        ),
    },
    disabled=[
        "transaction_date",
        "description",
        "merchant",
        "amount",
        "transaction_type",
    ],
)

if st.button("Save category changes", type="primary"):
    try:
        changed = save_category_changes(
            database,
            review_data,
            edited_data,
        )

        st.session_state.data = load_transactions(database)
        st.success(f"Saved {changed} category change(s).")
        st.rerun()

    except Exception as error:
        st.exception(error)

st.divider()
show_forecast(filtered)

st.divider()
st.subheader("Email summary")

recipient = st.text_input("Recipient email address")

if st.button("Send summary email"):
    if not recipient:
        st.warning("Enter a recipient email address.")
    else:
        summary = (
            f"Transactions: {len(filtered)}\n"
            f"Spending: ${debits:,.2f}\n"
            f"Income: ${credits:,.2f}\n"
            f"Net: ${credits - debits:,.2f}\n"
            f"Period: {start_date} to {end_date}\n"
        )

        try:
            send_email(summary, recipient)
            st.success("Summary email sent.")
        except Exception as error:
            st.exception(error)
