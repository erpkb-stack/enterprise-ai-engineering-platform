import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib";

const badgeVariants = cva("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4", {
  variants: {
    variant: {
      default: "border-transparent bg-accent text-accent-foreground",
      outline: "text-foreground",
      success: "border-transparent bg-success/15 text-success",
      warning: "border-transparent bg-warning/15 text-warning",
      danger: "border-transparent bg-danger/15 text-danger",
      info: "border-transparent bg-info/15 text-info",
      muted: "border-transparent bg-muted text-muted-foreground",
      brand: "border-transparent bg-brand text-white",
    },
  },
  defaultVariants: { variant: "default" },
});

export function Badge({ className, variant, ...p }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...p} />;
}
