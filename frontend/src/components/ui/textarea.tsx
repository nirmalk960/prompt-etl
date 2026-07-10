import * as React from "react";
import { cn } from "@/lib/utils";

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "glass-input w-full rounded-xl px-3 py-2 text-sm leading-relaxed text-foreground placeholder:text-muted-foreground/70",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";
