import calendar
from collections import defaultdict

import numpy as np
import pandas as pd

from app.api.deps import DateFilterType

from .utils import get_month_weeks, get_week_range, return_base


def get_percentage(past, actual):
    if past == actual or actual == 0:
        return 0

    if past == 0:
        return 100

    return round(((actual - past) / abs(past)) * 100, 2)


def get_df(expenses, incomes, transfers, accounts, places, categories):
    def get_account_id(row):
        return row["account_id"]

    def get_category_name(row):
        category_id = row["category_id"]
        if pd.notna(category_id) and category_id in categories_df.index:
            return categories_df.loc[category_id, "name"]

        return None

    def get_subcategory_name(row):
        subcategory_id = row["subcategory_id"]
        if pd.notna(subcategory_id) and subcategory_id in subcategories_df.index:
            return subcategories_df.loc[subcategory_id, "name"]

        return None

    def get_income_category_id(row):
        subcategory_id = row["subcategory_id"]
        if pd.notna(subcategory_id) and subcategory_id in subcategories_df.index:
            return subcategories_df.loc[subcategory_id, "category_id"]
        return None

    def get_place_name(row):
        place_id = row["place_id"]
        if pd.notna(place_id) and place_id in places_df.index:
            return places_df.loc[place_id, "name"]

        return None

    def get_category_color(row):
        category_id = row["category_id"]
        if pd.notna(category_id) and category_id in categories_df.index:
            return categories_df.loc[category_id, "color"]
        return None

    def get_income_category_color(row):
        subcategory_id = row["subcategory_id"]
        if pd.notna(subcategory_id) and subcategory_id in subcategories_df.index:
            category_id = subcategories_df.loc[subcategory_id, "category_id"]
            if category_id in categories_df.index:
                return categories_df.loc[category_id, "color"]
        return None

    incomes_df = pd.DataFrame(incomes)
    expenses_df = pd.DataFrame(expenses)
    transfers_df = pd.DataFrame(transfers)
    accounts_df = pd.DataFrame(accounts)
    places_df = pd.DataFrame(places)
    categories_df = pd.DataFrame(categories)

    categories_df.set_index("id", inplace=True)

    subcategories = []
    for category in categories_df.index:
        for subcategory in categories_df.loc[category, "subcategories"]:
            subcategories.append(subcategory)

    subcategories_df = pd.DataFrame(subcategories)
    subcategories_df.set_index("id", inplace=True)

    if not places_df.empty:
        places_df.set_index("id", inplace=True)

    if not accounts_df.empty:
        accounts_df.set_index("id", inplace=True)

    if not expenses_df.empty:
        expenses_df["type"] = "expense"
        expenses_df["amount"] = -expenses_df["amount"]
        expenses_df.set_index("id", inplace=True)

        expenses_df["place"] = expenses_df.apply(get_place_name, axis=1)
        expenses_df["account"] = expenses_df.apply(get_account_id, axis=1)
        expenses_df["category"] = expenses_df.apply(get_category_name, axis=1)
        expenses_df["subcategory"] = expenses_df.apply(get_subcategory_name, axis=1)
        expenses_df["category_color"] = expenses_df.apply(get_category_color, axis=1)

        expenses_df.drop(
            columns=[
                "account_id",
                "place_id",
                "owner_id",
            ],
            inplace=True,
        )

    if not incomes_df.empty:
        incomes_df["type"] = "income"
        incomes_df.set_index("id", inplace=True)

        incomes_df["account"] = incomes_df.apply(get_account_id, axis=1)
        incomes_df["place"] = incomes_df.apply(get_place_name, axis=1)
        incomes_df["subcategory"] = incomes_df.apply(get_subcategory_name, axis=1)
        incomes_df["category_id"] = incomes_df.apply(get_income_category_id, axis=1)
        incomes_df["category"] = incomes_df.apply(get_category_name, axis=1)
        incomes_df["category_color"] = incomes_df.apply(get_income_category_color, axis=1)

        incomes_df.drop(
            columns=["account_id", "place_id", "owner_id"],
            inplace=True,
        )

    if not transfers_df.empty:
        transfers_df["type"] = "transfer"
        transfers_df.set_index("id", inplace=True)
        transfers_df.rename(columns={"from_acc": "from_account_id", "to_acc": "to_account_id"}, inplace=True)

        transfers_df.drop(
            columns=["description", "owner_id"],
            inplace=True,
        )

    return {
        "expenses": expenses_df,
        "incomes": incomes_df,
        "transfers": transfers_df,
        "accounts": accounts_df,
        "places": places_df,
        "categories": categories_df,
        "subcategories": subcategories_df,
    }


def transaction_charts(date_filter_type, incomes_df, expenses_df):
    combined_df = pd.concat([expenses_df, incomes_df], ignore_index=True)
    income_color = incomes_df["category_color"].iloc[0] if not incomes_df.empty else "#4aae27"

    # Make df with all months
    all_months = pd.DataFrame({"month": calendar.month_name[1:]})

    # Convert date to datetime and add month column
    combined_df["date"] = pd.to_datetime(combined_df["date"])
    combined_df["month"] = combined_df["date"].dt.strftime("%B")

    # Sort by date
    combined_df.sort_values(by="date", inplace=True)

    # Add quarter column (quarter)
    combined_df["quarter"] = combined_df["date"].dt.quarter

    # Add day of week column (week)
    combined_df["day_of_week"] = combined_df["date"].dt.day_name()

    # Add week number (month)
    combined_df["week"] = combined_df["date"].dt.strftime("%W")
    combined_df["week"] = combined_df["week"].astype(np.int8)

    if date_filter_type == DateFilterType.year:
        total = all_months.merge(
            combined_df.groupby("month", as_index=False)["amount"].sum(),
            on="month",
            how="left",
        ).fillna(0)
        expenses = all_months.merge(
            combined_df[combined_df["type"] == "expense"]
            .groupby("month", as_index=False)["amount"]
            .sum(),
            on="month",
            how="left",
        ).fillna(0)
        expenses["amount"] = expenses["amount"].abs()
        incomes = all_months.merge(
            combined_df[combined_df["type"] == "income"]
            .groupby("month", as_index=False)["amount"]
            .sum(),
            on="month",
            how="left",
        ).fillna(0)

        return return_base(
            xAxis=total["month"].tolist(),
            total=total["amount"].tolist(),
            expenses=expenses["amount"].tolist(),
            incomes=incomes["amount"].tolist(),
            income_color=income_color,
        )

    if date_filter_type == DateFilterType.quarter:
        # Get the quarter from the actual data
        current_quarter = combined_df.iloc[0]["quarter"]

        all_months["quarter"] = all_months["month"].map(
            {
                "January": 1,
                "February": 1,
                "March": 1,
                "April": 2,
                "May": 2,
                "June": 2,
                "July": 3,
                "August": 3,
                "September": 3,
                "October": 4,
                "November": 4,
                "December": 4,
            }
        )

        # Filter all_months to only include months in the current quarter
        quarter_months = all_months[all_months["quarter"] == current_quarter].copy()

        quarter_total = quarter_months.merge(
            combined_df.groupby("month", as_index=False)["amount"].sum(),
            on="month",
            how="left",
        ).fillna(0)

        quarter_expenses = quarter_months.merge(
            combined_df[combined_df["type"] == "expense"]
            .groupby("month", as_index=False)["amount"]
            .sum(),
            on="month",
            how="left",
        ).fillna(0)

        quarter_incomes = quarter_months.merge(
            combined_df[combined_df["type"] == "income"]
            .groupby("month", as_index=False)["amount"]
            .sum(),
            on="month",
            how="left",
        ).fillna(0)

        quarter_expenses["amount"] = quarter_expenses["amount"].abs()

        return return_base(
            xAxis=quarter_total["month"].tolist(),
            total=quarter_total["amount"].tolist(),
            expenses=quarter_expenses["amount"].tolist(),
            incomes=quarter_incomes["amount"].tolist(),
            income_color=income_color,
        )

    if date_filter_type == DateFilterType.month:
        month_ranges = {"week": [], "range": []}

        week_start, week_end = get_month_weeks(
            combined_df.iloc[0]["date"].year, combined_df.iloc[0]["date"].month
        )

        for i in range(week_start, week_end + 1):
            data = get_week_range(combined_df.iloc[0]["date"].year, i)
            month_ranges["week"].append(data["week"])
            month_ranges["range"].append(data["range"])

        month_df = pd.DataFrame(month_ranges)

        month_df = pd.DataFrame(month_ranges)

        month_total = month_df.merge(
            combined_df.groupby("week", as_index=False)["amount"].sum(),
            on="week",
            how="left",
        ).fillna(0)
        month_incomes = month_df.merge(
            combined_df[combined_df["type"] == "income"]
            .groupby("week", as_index=False)["amount"]
            .sum(),
            on="week",
            how="left",
        ).fillna(0)
        month_expenses = month_df.merge(
            combined_df[combined_df["type"] == "expense"]
            .groupby("week", as_index=False)["amount"]
            .sum(),
            on="week",
            how="left",
        ).fillna(0)
        month_expenses["amount"] = month_expenses["amount"].abs()

        return return_base(
            xAxis=month_total["range"].tolist(),
            total=month_total["amount"].tolist(),
            expenses=month_expenses["amount"].tolist(),
            incomes=month_incomes["amount"].tolist(),
            income_color=income_color,
        )

    if date_filter_type == DateFilterType.week:
        all_days = pd.DataFrame(
            {
                "day_of_week": [
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                ]
            }
        )

        week_total = all_days.merge(
            combined_df.groupby("day_of_week", as_index=False)["amount"].sum(),
            on="day_of_week",
            how="left",
        ).fillna(0)
        week_incomes = all_days.merge(
            combined_df[combined_df["type"] == "income"]
            .groupby("day_of_week", as_index=False)["amount"]
            .sum(),
            on="day_of_week",
            how="left",
        ).fillna(0)
        week_incomes["amount"].abs()
        week_expenses = all_days.merge(
            combined_df[combined_df["type"] == "expense"]
            .groupby("day_of_week", as_index=False)["amount"]
            .sum(),
            on="day_of_week",
            how="left",
        ).fillna(0)
        week_expenses["amount"] = week_expenses["amount"].abs()

        return return_base(
            xAxis=week_total["day_of_week"].tolist(),
            total=week_total["amount"].tolist(),
            expenses=week_expenses["amount"].tolist(),
            incomes=week_incomes["amount"].tolist(),
            income_color=income_color,
        )

    if (
        date_filter_type == DateFilterType.date
        or date_filter_type == DateFilterType.range
    ):
        date_total = combined_df.groupby("date", as_index=False)["amount"].sum()
        date_incomes = (
            combined_df[combined_df["type"] == "income"]
            .groupby("date", as_index=False)["amount"]
            .sum()
        )
        date_expenses = (
            combined_df[combined_df["type"] == "expense"]
            .groupby("date", as_index=False)["amount"]
            .sum()
        )

        date_expenses["amount"] = date_expenses["amount"].abs()

        # convert datetime to string YYYY-MM-DD
        date_total["date"] = date_total["date"].dt.strftime("%Y-%m-%d")
        date_incomes["date"] = date_incomes["date"].dt.strftime("%Y-%m-%d")
        date_expenses["date"] = date_expenses["date"].dt.strftime("%Y-%m-%d")

        return return_base(
            xAxis=date_total["date"].tolist(),
            total=date_total["amount"].tolist(),
            expenses=date_incomes["amount"].tolist(),
            incomes=date_expenses["amount"].tolist(),
            income_color=income_color
        )


def categories_charts(incomes_df, expenses_df):
    if incomes_df.empty and expenses_df.empty:
        return None

    if incomes_df.empty:
        incomes_df = pd.DataFrame(columns=["category", "subcategory", "amount", "category_color"])

    if expenses_df.empty:
        expenses_df = pd.DataFrame(columns=["category", "subcategory", "amount", "category_color"])

    combined_df = pd.concat(
        [expenses_df, incomes_df], ignore_index=True
    )

    data_df = (
        combined_df.groupby(["category", "subcategory", "category_color", "type"])["amount"]
        .sum()
        .fillna(0)
        .reset_index()
    )
    data_df["amount"] = data_df["amount"].abs()

    # Calculate total amount per category for sorting
    category_totals = data_df.groupby("category")["amount"].sum().reset_index()
    category_totals = category_totals.sort_values("amount", ascending=False)

    result = []

    # Iterate through categories in descending order by amount
    for category in category_totals["category"]:
        group = data_df[data_df["category"] == category]
        category_data = {"name": category, "data": []}

        # Sort subcategories by amount in descending order
        group_sorted = group.sort_values("amount", ascending=False)

        for _, row in group_sorted.iterrows():
            subcategory_data = {"name": row["subcategory"], "value": round(row["amount"], 2)}
            category_data["data"].append(subcategory_data)

        result.append(category_data)

    cats_df = (
        data_df.groupby(["category", "category_color"])["amount"]
        .sum()
        .fillna(0)
        .reset_index()
    )

    # Sort categories by amount in descending order
    cats_df = cats_df.sort_values("amount", ascending=False)

    return {
        "drilldown": result,
        "categories": [
            {"name": row["category"], "value": round(row["amount"], 2), "color": row["category_color"]}
            for _, row in cats_df.iterrows()
        ],
    }


def accounts_total(incomes_df, expenses_df):
    if incomes_df.empty and expenses_df.empty:
        return {}

    if incomes_df.empty:
        return expenses_df.groupby("account")["amount"].sum().to_dict()

    if expenses_df.empty:
        return incomes_df.groupby("account")["amount"].sum().to_dict()

    combined_df = pd.concat([expenses_df, incomes_df], sort=False)

    return combined_df.groupby("account")["amount"].sum().to_dict()


def account_diff(past, actual):
    result = {}

    for account, value in actual.items():
        if account in past:
            result[account] = get_percentage(past[account], value)
        else:
            if value > 0:
                result[account] = 100
            else:
                result[account] = -100

    return result


def account_charts(incomes_df, expenses_df, transfers_df):
    if incomes_df.empty and expenses_df.empty and transfers_df.empty:
        return {}

    if not transfers_df.empty:
        # Create from_account entries (negative amount)
        from_transfers = transfers_df.copy()
        from_transfers['account'] = from_transfers['from_account_id']
        from_transfers['amount'] = -from_transfers['amount']  # Negative for outgoing

        # Create to_account entries (positive amount)
        to_transfers = transfers_df.copy()
        to_transfers['account'] = to_transfers['to_account_id']
        # Amount is already positive for incoming

        # Combine incomes and expenses into a single DataFrame
        transactions = pd.concat([incomes_df, expenses_df, from_transfers, to_transfers], ignore_index=True)
    else:
        transactions = pd.concat([incomes_df, expenses_df], ignore_index=True)

    # Sort the transactions by date
    transactions = transactions.sort_values("date")

    # Create a dictionary to store aggregated balances for each account
    account_data = defaultdict(lambda: {"xAxis": {"data": []}, "series": {"data": []}})

    # Aggregate transactions by date for each account
    for account_id, account_transactions in transactions.groupby("account"):
        if account_id is not None:  # Ensure we're not processing transactions with no account
            daily_balance = 0
            for date, day_transactions in account_transactions.groupby("date"):
                daily_balance += day_transactions["amount"].sum()

                account_data[account_id]["xAxis"]["data"].append(date)
                account_data[account_id]["series"]["data"].append(daily_balance)

    # Convert the defaultdict to a regular dict for JSON serialization
    return {str(k): v for k, v in account_data.items()}
