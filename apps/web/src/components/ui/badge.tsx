import * as React from "react";
import { cn } from "@/lib/utils";

export function Badge({
  className,
  variant = "default",
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & {
  variant?: "default" | "secondary" | "outline" | "success";
}) {
  const styles = {
    default: "bg-primary/15 text-indigo-300 border-primary/30",
    secondary: "bg-secondary text-secondary-foreground border-border",
    outline: "bg-transparent text-muted-foreground border-border",
    success: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  }[variant];

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium",
        styles,
        className
      )}
      {...props}
    />
  );
}

export function Alert({
  className,
  variant = "default",
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { variant?: "default" | "destructive" }) {
  return (
    <div
      role="alert"
      className={cn(
        "rounded-lg border px-4 py-3 text-sm",
        variant === "destructive"
          ? "border-red-500/40 bg-red-500/10 text-red-200"
          : "border-border bg-secondary/50 text-foreground",
        className
      )}
      {...props}
    />
  );
}
