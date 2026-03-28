import { ChevronLeft, ChevronRight, Calendar } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import type { DateFilterType } from "@/types/api";

interface DateFilterProps {
  dateFilterType: DateFilterType;
  date: string;
  onDateFilterTypeChange: (type: DateFilterType) => void;
  onDateChange: (date: string) => void;
}

function getDisplayLabel(dateFilterType: DateFilterType, date: string): string {
  switch (dateFilterType) {
    case "month": {
      const [y, m] = date.split("-");
      const monthName = new Date(Number(y), Number(m) - 1).toLocaleString(
        "en-US",
        { month: "long" },
      );
      return `${monthName} ${y}`;
    }
    case "year":
      return date;
    case "quarter": {
      const [y, q] = date.split("-");
      return `${q} ${y}`;
    }
    case "week":
    case "date":
      return date;
    case "range": {
      const [start, end] = date.split(":");
      return `${start} - ${end}`;
    }
    default:
      return date;
  }
}

function navigateDate(
  dateFilterType: DateFilterType,
  date: string,
  direction: -1 | 1,
): string {
  switch (dateFilterType) {
    case "month": {
      const [y, m] = date.split("-").map(Number);
      const d = new Date(y, m - 1 + direction, 1);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    }
    case "year": {
      return String(Number(date) + direction);
    }
    case "week": {
      const d = new Date(date);
      d.setDate(d.getDate() + direction * 7);
      return d.toISOString().slice(0, 10);
    }
    case "date": {
      const d = new Date(date);
      d.setDate(d.getDate() + direction);
      return d.toISOString().slice(0, 10);
    }
    case "quarter": {
      const [y, qStr] = date.split("-");
      const qNum = Number(qStr.replace("Q", ""));
      let newQ = qNum + direction;
      let newY = Number(y);
      if (newQ > 4) {
        newQ = 1;
        newY++;
      } else if (newQ < 1) {
        newQ = 4;
        newY--;
      }
      return `${newY}-Q${newQ}`;
    }
    default:
      return date;
  }
}

export function DateFilter({
  dateFilterType,
  date,
  onDateFilterTypeChange,
  onDateChange,
}: DateFilterProps) {
  const canNavigate = dateFilterType !== "range";

  return (
    <div className="flex items-center gap-3">
      <Calendar className="text-muted-foreground h-4 w-4" />

      <Select
        value={dateFilterType}
        onChange={(e) =>
          onDateFilterTypeChange(e.target.value as DateFilterType)
        }
        className="w-32"
      >
        <option value="month">Month</option>
        <option value="week">Week</option>
        <option value="quarter">Quarter</option>
        <option value="year">Year</option>
        <option value="date">Day</option>
      </Select>

      <div className="flex items-center gap-1">
        {canNavigate && (
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onDateChange(navigateDate(dateFilterType, date, -1))}
            className="h-8 w-8"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
        )}

        <span className="min-w-[140px] text-center text-sm font-medium">
          {getDisplayLabel(dateFilterType, date)}
        </span>

        {canNavigate && (
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onDateChange(navigateDate(dateFilterType, date, 1))}
            className="h-8 w-8"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
