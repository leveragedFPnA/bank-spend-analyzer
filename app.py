import io
import re
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Personal Spend Analyzer",
    page_icon="₹",
    layout="wide",
)

RULES = {
    "Food & Dining": ["swiggy", "zomato", "restaurant", "cafe", "coffee", "food"],
    "Transport": ["uber", "ola", "rapido", "metro", "petrol", "fuel", "parking"],
    "Bills & Utilities": ["electricity", "airtel", "jio", "broadband", "recharge"],
    "Subscriptions": ["netflix", "spotify", "prime", "jiohotstar", "youtube"],
    "Shopping": ["amazon", "flipkart", "myntra", "shopping", "mall"],
    "Groceries": ["bigbasket", "blinkit", "zepto", "dmart", "grocery"],
    "Health": ["pharmacy", "hospital", "clinic", "apollo", "medplus"],
    "Income": ["salary", "payroll", "interest credit", "refund", "cashback"],
    "Transfers": ["transfer", "neft", "imps", "rtgs"],
}


def amount(value):
    text = str(value).strip()

    if not text:
        return 0.0

    text = re.sub(r"[^0-9.]", "", text)

    try:
        return float(text)
    except ValueError:
        return 0.0


def category(description):
    text = str(description).lower()

    for name, keywords in RULES.items():
        if any(keyword in text for keyword in keywords):
            return name

    return "Other"


def find_position(header, labels):
    header_lower = header.lower()

    for label in labels:
        position = header_lower.find(label.lower())
        if position >= 0:
            return position

    return -1


def field(line, start, end=None):
    line = line.ljust(180)

    if end is None:
        return line[start:].strip()

    return line[start:end].strip()


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
        raise ValueError("Bank statement header was not found.")

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

    if any(position < 0 for position in positions.values()):
        raise ValueError(
            "One or more required statement columns were not found."
        )

    transaction_start = re.compile(
        r"^\s*\d{2}/\d{2}/\d{2,4}\b"
    )

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
                "Date": field(
                    line,
                    positions["date"],
                    positions["narration"],
                ),
                "Narration": field(
                    line,
                    positions["narration"],
                    positions["reference"],
                ),
                "Reference": field(
                    line,
                    positions["reference"],
                    positions["value_date"],
                ),
                "Value Date": field(
                    line,
                    positions["value_date"],
                    positions["withdrawal"],
                ),
                "Withdrawal": field(
                    line,
                    positions["withdrawal"],
                    positions["deposit"],
                ),
                "Deposit": field(
                    line,
                    positions["deposit"],
                    positions["balance"],
                ),
                "Balance": field(
                    line,
                    positions["balance"],
                ),
            }
        elif current:
            current["Narration"] = (
                current["Narration"] + " " + stripped
            ).strip()

    if current:
        rows.append(current)

    if not rows:
        raise ValueError("No transactions were found.")

    data = pd.DataFrame(rows)

    data["Date"] = pd.to_datetime(
        data["Date"],
        format="%d/%m/%y",
        errors="coerce",
    )

    data["Withdrawal"] = data["Withdrawal"].map(amount)
    data["Deposit"] = data["Deposit"].map(amount)
    data["Balance"] = data["Balance"].map(amount)

    data = data.dropna(subset=["Date"]).copy()
    data = data[
        (data["Withdrawal"] > 0)
        | (data["Deposit"] > 0)
    ].copy()

    data["Type"] = data.apply(
        lambda row: "Expense"
        if row["Withdrawal"] > 0
        else "Income",
        axis=1,
    )
    data["Category"] = data["Narration"].map(category)
    data["Month"] = data["Date"].dt.to_period("M").astype(str)

    data.loc[
        (data["Deposit"] > 0)
        & (data["Category"] == "Other"),
        "Category",
    ] = "Income"

    return data.sort_values("Date").reset_index(drop=True)


def money(value):
    return f"₹{value:,.2f}"


st.title("Personal Bank Spend Analyzer")
st.write(
    "Upload a fixed-width TXT bank statement. "
    "The file is processed for this session only."
)

uploaded = st.file_uploader(
    "Choose your bank statement",
    type=["txt"],
)

if uploaded is None:
    st.info("Upload a TXT statement to begin.")
    st.stop()

try:
    transactions = parse_txt(uploaded.getvalue())
except Exception as error:
    st.error(f"Could not read the statement: {error}")
    st.stop()

if transactions.empty:
    st.warning("No transactions were detected.")
    st.stop()

with st.sidebar:
    st.header("Filters")

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

    selected_dates = st.date_input(
        "Date range",
        value=(minimum_date, maximum_date),
        min_value=minimum_date,
        max_value=maximum_date,
    )

if len(selected_dates) == 2:
    start_date, end_date = selected_dates
else:
    start_date = end_date = selected_dates[0]

filtered = transactions[
    transactions["Category"].isin(selected_categories)
    & transactions["Date"].dt.date.between(
        start_date,
        end_date,
    )
].copy()

if filtered.empty:
    st.warning("No transactions match the selected filters.")
    st.stop()

total_spending = filtered["Withdrawal"].sum()
total_income = filtered["Deposit"].sum()
net_flow = total_income - total_spending

one, two, three, four = st.columns(4)

one.metric("Total spending", money(total_spending))
two.metric("Total income", money(total_income))
three.metric("Net cash flow", money(net_flow))
four.metric("Transactions", f"{len(filtered):,}")

tab_one, tab_two, tab_three = st.tabs(
    ["Overview", "Transactions", "Review"]
)

with tab_one:
    category_totals = (
        filtered.groupby("Category")["Withdrawal"]
        .sum()
        .sort_values(ascending=False)
    )

    monthly_totals = (
        filtered.groupby("Month")["Withdrawal"]
        .sum()
    )

    left, right = st.columns(2)

    with left:
        st.subheader("Spending by category")
        st.bar_chart(category_totals)

    with right:
        st.subheader("Monthly spending")
        st.line_chart(monthly_totals)

    st.subheader("Largest expenses")
    largest = filtered.sort_values(
        "Withdrawal",
        ascending=False,
    ).head(10)

    st.dataframe(
        largest[
            [
                "Date",
                "Narration",
                "Category",
                "Withdrawal",
                "Balance",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

with tab_two:
    display = filtered.copy()
    display["Date"] = display["Date"].dt.strftime(
        "%d/%m/%Y"
    )

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
    )

    csv_data = display.to_csv(index=False).encode(
        "utf-8"
    )

    st.download_button(
        "Download analysis",
        data=csv_data,
        file_name="spend_analysis.csv",
        mime="text/csv",
    )

with tab_three:
    other = filtered[
        filtered["Category"] == "Other"
    ]

    st.subheader("Transactions needing categorization")
    st.dataframe(
        other[
            [
                "Date",
                "Narration",
                "Withdrawal",
                "Deposit",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.metric(
        "Uncategorized transactions",
        f"{len(other):,}",
    )

st.caption(
    "This MVP uses transparent Python rules. "
    "No external AI service or database is used."
)
