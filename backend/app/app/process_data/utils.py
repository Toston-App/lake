import datetime


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
    last_day = first_day + datetime.timedelta(days=32) - datetime.timedelta(days=1)

    return [int(first_day.strftime("%W")), int(last_day.strftime("%W"))]


def return_base(xAxis, total, expenses, incomes):
    return {
        "series": [
            {"name": "Total", "data": total},
            {"name": "Expenses", "data": expenses},
            {"name": "Incomes", "data": incomes},
        ],
        "xAxis": xAxis,
    }
