import hashlib
import hmac
import io
import re
import smtplib
from datetime import date
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


def require_app_password():
    settings = secret_section("app")
    expected = settings.get("access_password")

    if not expected:
        return True

    if st.session_state.get("authenticated"):
        return True

    st.title("Bank Spend Analyzer")
    entered = st.text_input("Enter your private app password", type="password")

    if st.button("Unlock", type="primary"):
        if hmac.compare_digest(entered, expected):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    st.stop()


def get_supabase():
    if create_client is None:
        return None, "Install the supabase package."

    settings = secret_section("supabase")
    url = settings.get("url")
    key = settings.get("key")

    if not url or not key:
        return None, "Supabase settings are missing from Streamlit Secrets."

    try:
        return create_client(url, key), None
    except Exception as exc:
        return None, str(exc)


def normalize_text(value):
    value = "" if pd.isna(value) else str(value)
    return re.sub(r"\s+", " ", value.strip().lower())


def find_column(df, keywords):
    for column in df.columns:
        name = normalize_text(column).replace("_", " ")
        if any(keyword in name for keyword in keywords):
            return column
    return None


def parse_amount(value):
    if pd.isna(value) or str(value).strip() == "":
        return None

    text = str(value).strip().replace("$", "").replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.replace("(", "").replace(")", "")
    text = re.sub(r"[^0-9.\-]", "", text)

    number = pd.to_numeric(text, errors="coerce")
    if pd.isna(number):
        return None

    return -abs(float(number)) if negative else float(number)


def merchant_name(description):
    words = re.findall(r"[a-z0-9]+", normalize_text(description))
    words = [word for word in words if len(word) > 2]
    return " ".join(words[:5]) or "Unknown merchant"


def classify_transaction(description, transaction_type):
    text = normalize_text(description)
    tx_type = normalize_text(transaction_type)

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

    if tx_type == "credit" and any(
        term in text for term in ["deposit", "refund", "interest", "dividend"]
    ):
        return "Income"

    category_terms = {
        "Housing": ["rent", "mortgage", "hoa", "property management"],
        "Utilities": ["electric", "water", "gas bill", "internet", "verizon", "t-mobile"],
        "Groceries": ["grocery", "market", "aldi", "kroger", "whole foods", "walmart"],
        "Food & Dining": ["restaurant", "cafe", "coffee", "doordash", "ubereats", "grubhub"],
        "Transportation": ["uber", "lyft", "transit", "parking", "toll"],
        "Fuel": ["shell", "chevron", "exxon", "bp", "fuel", "gas station"],
        "Shopping": ["amazon", "target", "costco", "mall", "store"],
        "Entertainment": ["netflix", "spotify", "movie", "theater", "hulu"],
        "Healthcare": ["medical", "pharmacy", "doctor", "dental", "health"],
        "Debt Payments": ["loan", "credit card payment", "capital one payment"],
        "Transfers": ["transfer", "zelle", "venmo", "cash app"],
        "Bank Fees": ["fee", "service charge", "overdraft"],
        "Taxes": ["irs", "tax", "state tax"],
    }

    for category, terms in category_terms.items():
        if any(term in text for term in terms):
            return category

    return "Other"


def standardize_dataframe(raw_df, source_file):
    df = raw_df.copy()
    df = df.dropna(how="all")

    if df.empty:
        raise ValueError("The uploaded file contains no usable rows.")

    df.columns = [str(column).strip() for column in df.columns]

    date_column = find_column(
        df,
        ["date", "posted", "transaction date", "trans date"],
    )
    description_column = find_column(
        df,
        ["description", "memo", "details", "merchant", "payee", "name"],
    )
    amount_column = find_column(
        df,
        ["amount", "transaction amount", "debit amount", "credit amount"],
    )
    debit_column = find_column(df, ["debit", "withdrawal", "charge"])
    credit_column = find_column(df, ["credit", "deposit"])

    if date_column is None:
        raise ValueError("Could not find a transaction date column.")

    result = pd.DataFrame()
    result["transaction_date"] = pd.to_datetime(
        df[date_column],
        errors="coerce",
    ).dt.date

    if description_column:
        result["description"] = df[description_column].fillna("").astype(str)
    else:
        text_columns = df.select_dtypes(include=["object"]).columns
        result["description"] = (
            df[text_columns].fillna("").astype(str).agg(" ".join, axis=1)
        )

    if debit_column or credit_column:
        debit_values = (
            df[debit_column].apply(parse_amount)
            if debit_column
            else pd.Series(0.0, index=df.index)
        )
        credit_values = (
            df[credit_column].apply(parse_amount)
            if credit_column
            else pd.Series(0.0, index=df.index)
        )

        debit_values = pd.to_numeric(debit_values, errors="coerce").fillna(0)
        credit_values = pd.to_numeric(credit_values, errors="coerce").fillna(0)

        result["amount"] = debit_values.abs().where(
            debit_values.abs() > 0,
            credit_values.abs(),
        )
        result["transaction_type"] = debit_values.abs().where(
            debit_values.abs() > 0,
            credit_values.abs(),
        ).apply(lambda value: "Debit" if value > 0 else "Credit")
    elif amount_column:
        raw_amounts = df[amount_column].apply(parse_amount)
        result["amount"] = raw_amounts.abs()
        result["transaction_type"] = raw_amounts.apply(
            lambda value: "Debit"
            if pd.notna(value) and value < 0
            else "Credit"
        )
    else:
        raise ValueError("Could not find an amount, debit, or credit column.")

    result["merchant"] = result["description"].apply(merchant_name)
    result["category"] = result.apply(
        lambda row: classify_transaction(
            row["description"],
            row["transaction_type"],
        ),
        axis=1,
    )
    result["source_file"] = source_file
    result["user_id"] = "personal"

    result = result.dropna(subset=["transaction_date", "amount"])
    result["amount"] = pd.to_numeric(result["amount"], errors="coerce")
    result = result.dropna(subset=["amount"])

    result["transaction_key"] = result.apply(
        lambda row: hashlib.sha256(
            "|".join(
                [
                    str(row["transaction_date"]),
                    normalize_text(row["description"]),
                    f"{float(row['amount']):.2f}",
                    normalize_text(row["transaction_type"]),
                ]
            ).encode("utf-8")
        ).hexdigest(),
        axis=1,
    )

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
        raise ValueError("Install pypdf to read PDF statements.")

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
            amount_value = parse_amount(amount_text)

            if amount_value is None:
                continue

            description = line.replace(date_text, "", 1)
            description = description.replace(amount_text, "", 1).strip(" -|")

            rows.append(
                {
                    "Date": date_text,
                    "Description": description,
                    "Amount": amount_value,
                }
            )

    if not rows:
        raise ValueError(
            "No transactions found in the PDF. A text-based PDF is required."
        )

    return pd.DataFrame(rows)


def parse_upload(uploaded_file):
    filename = uploaded_file.name.lower()

    if filename.endswith(".csv"):
        uploaded_file.seek(0)
        try:
            raw_df = pd.read_csv(uploaded_file)
        except Exception:
            uploaded_file.seek(0)
            raw_df = pd.read_csv(uploaded_file, sep=None, engine="python")
        return standardize_dataframe(raw_df, uploaded_file.name)

    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        uploaded_file.seek(0)
        raw_df = pd.read_excel(uploaded_file)
        return standardize_dataframe(raw_df, uploaded_file.name)

    if filename.endswith(".pdf"):
        uploaded_file.seek(0)
        raw_df = parse_pdf(uploaded_file)
        return standardize_dataframe(raw_df, uploaded_file.name)

    if filename.endswith(".txt"):
        uploaded_file.seek(0)
        content = uploaded_file.read().decode("utf-8", errors="ignore")
        raw_df = pd.read_csv(io.StringIO(content), sep=None, engine="python")
        return standardize_dataframe(raw_df, uploaded_file.name)

    raise ValueError("Supported files are CSV, XLSX, XLS, PDF, and TXT.")


def load_transactions(db):
    if db is None:
        return pd.DataFrame()

    response = (
        db.table("transactions")
        .select("*")
        .eq("user_id", "personal")
        .order("transaction_date", desc=True)
        .execute()
    )

    rows = response.data or []
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["transaction_date"] = pd.to_datetime(
        df["transaction_date"],
        errors="coerce",
    ).dt.date
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df["category"] = df["category"].fillna("Other")
    df["merchant"] = df["merchant"].fillna("")
    return df


def load_rules(db):
    if db is None:
        return {}

    response = (
        db.table("category_rules")
        .select("merchant,final_category,pattern,category")
        .eq("user_id", "personal")
        .execute()
    )

    rules = {}
    for row in response.data or []:
        merchant = row.get("merchant") or row.get("pattern")
        category = row.get("final_category") or row.get("category")

        if merchant and category:
            rules[normalize_text(merchant)] = category

    return rules


def apply_saved_rules(df, rules):
    result = df.copy()

    for index, row in result.iterrows():
        merchant = normalize_text(row.get("merchant", ""))
        if merchant in rules:
            result.at[index, "category"] = rules[merchant]

    return result


def save_transactions(db, df):
    if db is None or df.empty:
        return

    records = []
    for row in df.to_dict("records"):
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

    for start in range(0, len(records), 500):
        db.table("transactions").upsert(
            records[start : start + 500],
            on_conflict="transaction_key",
        ).execute()


def save_category_changes(db, original_df, edited_df):
    changed = 0
    rules = {}

    original_categories = original_df.set_index(
        "transaction_key"
    )["category"].to_dict()

    for _, row in edited_df.iterrows():
        key = str(row.get("transaction_key", "")).strip()
        new_category = str(row.get("category", "Other")).strip() or "Other"

        if not key or key not in original_categories:
            continue

        old_category = str(original_categories[key])
        if old_category == new_category:
            continue

        if db is not None:
            (
                db.table("transactions")
                .update({"category": new_category})
                .eq("transaction_key", key)
                .execute()
            )

        merchant = str(row.get("merchant", "")).strip()
        if merchant and merchant.lower() not in {"nan", "none"}:
            rules[normalize_text(merchant)] = {
                "user_id": "personal",
                "merchant": merchant,
                "final_category": new_category,
                "pattern": merchant,
                "category": new_category,
            }

        changed += 1

    if db is not None:
        for rule in rules.values():
            (
                db.table("category_rules")
                .upsert(
                    rule,
                    on_conflict="user_id,merchant",
                )
                .execute()
            )

    return changed


def send_email_summary(summary_text, recipient):
    settings = secret_section("gmail")
    sender = settings.get("address")
    app_password = settings.get("app_password")

    if not sender or not app_password:
        raise ValueError(
            "Add gmail.address and gmail.app_password to Streamlit Secrets."
        )

    message = MIMEText(summary_text)
    message["Subject"] = "Bank Spend Analyzer Summary"
    message["From"] = sender
    message["To"] = recipient

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender, app_password)
        server.send_message(message)


def show_forecast(df):
    expenses = df[
        df["transaction_type"].str.lower().eq("debit")
    ].copy()

    if expenses.empty:
        st.info("No debit transactions are available for forecasting.")
        return

    expenses["month"] = pd.to_datetime(
        expenses["transaction_date"]
    ).dt.to_period("M").astype(str)

    monthly = expenses.groupby("month")["amount"].sum().sort_index()
    average_monthly = monthly.tail(3).mean()

    st.subheader("Spending forecast")
    col1, col2 = st.columns(2)
    col1.metric("Average monthly spending", f"${average_monthly:,.2f}")
    col2.metric("Next-month estimate", f"${average_monthly:,.2f}")

    if not monthly.empty:
        st.line_chart(monthly)


require_app_password()

st.title("Bank Spend Analyzer")
st.caption("Upload statements, review every transaction, and save categories.")

db, db_error = get_supabase()

if db_error:
    st.sidebar.warning(db_error)

if "data" not in st.session_state:
    try:
        st.session_state.data = load_transactions(db)
    except Exception as exc:
        st.session_state.data = pd.DataFrame()
        st.sidebar.error(f"Saved transactions could not be loaded: {exc}")

with st.sidebar:
    st.header("Import statement")
    uploaded_file = st.file_uploader(
        "Upload CSV, Excel, PDF, or TXT",
        type=["csv", "xlsx", "xls", "pdf", "txt"],
    )

    if st.button("Import statement", type="primary"):
        if uploaded_file is None:
            st.warning("Choose a statement file first.")
        else:
            try:
                imported = parse_upload(uploaded_file)
                rules = load_rules(db)
                imported = apply_saved_rules(imported, rules)

                if db is not None:
                    save_transactions(db, imported)
                    st.session_state.data = load_transactions(db)
                else:
                    existing = st.session_state.data
                    st.session_state.data = pd.concat(
                        [existing, imported],
                        ignore_index=True,
                    ).drop_duplicates(
                        "transaction_key",
                        keep="last",
                    )

                st.success(f"Imported {len(imported)} transaction(s).")
                st.rerun()
            except Exception as exc:
                st.error(f"Import failed: {exc}")

data = st.session_state.data

if data is None or data.empty:
    st.info("Upload a statement to begin.")
    st.stop()

data["transaction_date"] = pd.to_datetime(
    data["transaction_date"],
    errors="coerce",
).dt.date
data["category"] = data["category"].fillna("Other")
data["merchant"] = data["merchant"].fillna("")

min_date = min(data["transaction_date"])
max_date = max(data["transaction_date"])

with st.sidebar:
    selected_dates = st.date_input(
        "Date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    selected_categories = st.multiselect(
        "Categories",
        options=CATEGORIES,
        default=CATEGORIES,
    )

if isinstance(selected_dates, (tuple, list)) and len(selected_dates) == 2:
    start_date, end_date = selected_dates
else:
    start_date = end_date = selected_dates

filtered = data[
    data["transaction_date"].between(start_date, end_date)
    & data["category"].isin(selected_categories)
].copy()

if filtered.empty:
    st.warning("No transactions match the selected filters.")
    st.stop()

debits = filtered[
    filtered["transaction_type"].str.lower().eq("debit")
]["amount"].sum()

credits = filtered[
    filtered["transaction_type"].str.lower().eq("credit")
]["amount"].sum()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Transactions", f"{len(filtered):,}")
col2.metric("Spending", f"${debits:,.2f}")
col3.metric("Income", f"${credits:,.2f}")
col4.metric("Net", f"${credits - debits:,.2f}")

st.subheader("Review and categorize all transactions")
st.caption("Edit the Category column for any transaction, then save your changes.")

review_columns = [
    "transaction_key",
    "transaction_date",
    "description",
    "merchant",
    "amount",
    "transaction_type",
    "category",
]

review_df = filtered[review_columns].copy()

edited_df = st.data_editor(
    review_df,
    hide_index=True,
    use_container_width=True,
    num_rows="fixed",
    key="transaction_editor",
    column_config={
        "transaction_key": None,
        "transaction_date": st.column_config.DateColumn("Date"),
        "description": st.column_config.TextColumn("Description"),
        "merchant": st.column_config.TextColumn("Merchant"),
        "amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
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
        changed_count = save_category_changes(db, review_df, edited_df)

        if db is None:
            updated = data.set_index("transaction_key")
            for _, row in edited_df.iterrows():
                key = row["transaction_key"]
                updated.loc[key, "category"] = row["category"]
            st.session_state.data = updated.reset_index()

        st.success(f"Saved {changed_count} category change(s).")
        st.rerun()
    except Exception as exc:
        st.error(f"Categories could not be saved: {exc}")

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
            f"Transactions reviewed: {len(filtered)}\n"
            f"Spending: ${debits:,.2f}\n"
            f"Income: ${credits:,.2f}\n"
            f"Net: ${credits - debits:,.2f}\n"
            f"Period: {start_date} to {end_date}\n"
        )

        try:
            send_email_summary(summary, recipient)
            st.success("Summary email sent.")
        except Exception as exc:
            st.error(f"Email could not be sent: {exc}")
