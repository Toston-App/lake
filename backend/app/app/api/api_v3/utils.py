import calendar
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException

from app.api.deps import DateFilterType


@dataclass
class DateRange:
    """Parsed date range with comparison metadata."""

    start_date: Date
    end_date: Date
    comparison_type: str  # "days" or "months"
    time_difference: int  # number of days or months for past-period offset

    @property
    def past_start_date(self) -> Date:
        """Start date of the comparison (past) period."""
        reldelta = (
            relativedelta(days=self.time_difference)
            if self.comparison_type == "days"
            else relativedelta(months=self.time_difference)
        )
        return self.start_date - reldelta

    @property
    def past_end_date(self) -> Date:
        """End date of the comparison (past) period."""
        reldelta = (
            relativedelta(days=self.time_difference)
            if self.comparison_type == "days"
            else relativedelta(months=self.time_difference)
        )
        return self.end_date - reldelta


def parse_date_range(date_filter_type: DateFilterType, date_param: str) -> DateRange:
    """
    Parse a date_filter_type + date string into a structured DateRange.
    Raises HTTPException(400) on invalid input.
    """
    if date_filter_type == DateFilterType.date:
        try:
            start_date = datetime.strptime(date_param, "%Y-%m-%d").date()
            return DateRange(
                start_date=start_date,
                end_date=start_date,
                comparison_type="days",
                time_difference=0,
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Date must be in the format YYYY-MM-DD",
            )

    elif date_filter_type == DateFilterType.week:
        try:
            start_date = datetime.strptime(date_param, "%Y-%m-%d").date()
            end_date = start_date + timedelta(days=6)
            return DateRange(
                start_date=start_date,
                end_date=end_date,
                comparison_type="days",
                time_difference=6,
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Date must be in the format YYYY-MM-DD",
            )

    elif date_filter_type == DateFilterType.month:
        try:
            start_date = datetime.strptime(date_param, "%Y-%m").date()
            _, num_days = calendar.monthrange(start_date.year, start_date.month)
            end_date = start_date + timedelta(days=num_days - 1)
            return DateRange(
                start_date=start_date,
                end_date=end_date,
                comparison_type="months",
                time_difference=1,
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Date must be in the format YYYY-MM",
            )

    elif date_filter_type == DateFilterType.quarter:
        try:
            year_str, quarter_str = date_param.split("-")
            quarter_num = int(quarter_str.replace("Q", ""))
            year = int(year_str)

            if quarter_num < 1 or quarter_num > 4:
                raise ValueError("Quarter must be between 1 and 4")

            start_month = (quarter_num - 1) * 3 + 1
            end_month = quarter_num * 3
            start_date = Date(year, start_month, 1)
            _, end_day = calendar.monthrange(year, end_month)
            end_date = Date(year, end_month, end_day)

            return DateRange(
                start_date=start_date,
                end_date=end_date,
                comparison_type="months",
                time_difference=3,
            )
        except (ValueError, IndexError):
            raise HTTPException(
                status_code=400,
                detail="Date must be in the format YYYY-QX",
            )

    elif date_filter_type == DateFilterType.year:
        try:
            year = int(date_param)
            start_date = Date(year, 1, 1)
            end_date = Date(year, 12, 31)
            return DateRange(
                start_date=start_date,
                end_date=end_date,
                comparison_type="days",
                time_difference=365,
            )
        except (ValueError, IndexError):
            raise HTTPException(
                status_code=400,
                detail="Date must be in the format YYYY",
            )

    elif date_filter_type == DateFilterType.range:
        try:
            start_date_str, end_date_str = date_param.split(":")
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Date range must be in the format YYYY-MM-DD:YYYY-MM-DD",
            )

        if start_date > end_date:
            raise HTTPException(
                status_code=400,
                detail="Start date must be before end date",
            )

        return DateRange(
            start_date=start_date,
            end_date=end_date,
            comparison_type="days",
            time_difference=(end_date - start_date).days,
        )

    raise HTTPException(status_code=400, detail="Invalid date filter type")
