import * as React from "react";
import { cn } from "@/lib/utils";
import { ChevronDown } from "lucide-react";

function Select({
  className,
  children,
  ...props
}: React.ComponentProps<"select">) {
  return (
    <div className="relative">
      <select
        className={cn(
          "border-input bg-background ring-offset-background focus:ring-ring flex h-9 w-full appearance-none items-center rounded-md border py-2 pr-8 pl-3 text-sm shadow-sm focus:outline-none focus:ring-1 disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown className="text-muted-foreground pointer-events-none absolute top-1/2 right-2 h-4 w-4 -translate-y-1/2" />
    </div>
  );
}

export { Select };
