import calendar
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
