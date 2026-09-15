import type { ReactNode } from "react";

import { cx } from "@/components/ui/cx";

export function SectionHeading({
  id,
  eyebrow,
  title,
  description,
  align = "center",
  inverse = false,
}: {
  /** Referenced by the section's `aria-labelledby`. */
  id: string;
  eyebrow?: string;
  title: ReactNode;
  description?: ReactNode;
  align?: "center" | "start";
  /** On a dark band, where colours must not follow the theme. */
  inverse?: boolean;
}) {
  return (
    <div className={cx("max-w-2xl", align === "center" && "mx-auto text-center")}>
      {eyebrow && <p className={cx("eyebrow", inverse && "text-[#a3b0ff]")}>{eyebrow}</p>}
      <h2 id={id} className={cx("heading-xl mt-3", inverse ? "text-night-ink" : "text-ink")}>
        {title}
      </h2>
      {description && (
        <p className={cx("lead mt-4", inverse && "text-night-muted")}>{description}</p>
      )}
    </div>
  );
}
