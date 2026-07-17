import calendar
import datetime


def calculate_summary(current_total: float, previous_total: float) -> dict:
    """
    Calculate period-over-period comparison summary.

    Returns:
        dict with current, previous, change, changePercent, isPositive

    Rules:
    - If previous_total is 0, changePercent is 0
    - If previous_total is negative, calculate percentage with absolute value
    - change and changePercent are always positive (use isPositive for direction)
    - All values rounded to 2 decimal places
    """
    change = current_total - previous_total

    # Handle edge cases per requirements
    if previous_total == 0:
        change_percent = 0.0
    else:
        # Calculate percentage even if previous is negative
        change_percent = abs((change / abs(previous_total)) * 100)

    return {
        "current": round(current_total, 2),
        "previous": round(previous_total, 2),
        "change": round(abs(change), 2),  # Always positive
        "changePercent": round(change_percent, 2),  # 2 decimal places
        "isPositive": change >= 0,
    }


def get_week_range(year, week):
    start_date = datetime.date(year, 1, 1)
    first_day = (
        start_date
        - datetime.timedelta(days=start_date.weekday())
        + datetime.timedelta(weeks=week if year != 2024 else week - 1)
    )
    last_day = first_day + datetime.timedelta(days=6)

    return {"week": week, "range": f"{first_day.day}-{last_day.day}"}


def get_month_weeks(year, month):
    first_day = datetime.date(year, month, 1)
    last_day = datetime.date(year, month, calendar.monthrange(year, month)[1])

    return [int(first_day.strftime("%W")), int(last_day.strftime("%W"))]


def return_base(xAxis, total, expenses, incomes, income_color):
    return {
        "series": [
            {"name": "Total", "data": total, "color": "#168fff"},
            {"name": "Expenses", "data": expenses, "color": "#e23670"},
            {"name": "Incomes", "data": incomes, "color": income_color},
        ],
        "xAxis": xAxis,
    }


def return_net(xAxis, total, summary=None):
    """
    Return net chart data with optional summary comparison.

    Args:
        xAxis: List of x-axis labels
        total: List of net totals (income - expenses)
        summary: Optional dict with comparison data
    """
    result = {
        "series": [
            {"name": "Net", "data": total, "color": "#168fff"},
        ],
        "xAxis": xAxis,
    }

    if summary:
        result["summary"] = summary

    return result


def return_income_vs_expense(xAxis, expenses, incomes, income_color):
    return {
        "series": [
            {"name": "Expenses", "data": expenses, "color": "#e23670"},
            {"name": "Incomes", "data": incomes, "color": income_color},
        ],
        "xAxis": xAxis,
    }
