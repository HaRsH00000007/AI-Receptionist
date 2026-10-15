"use client";

/**
 * Two ways to set up a receptionist, offered before either one starts.
 *
 * The website route is listed first because it is the one that saves work, but
 * it is not the default: a business with a thin or JavaScript-only site is
 * better served by the form, and nothing here promises a crawl will succeed.
 * The manual route is the existing wizard, unchanged.
 *
 * The choice is kept in the URL (`?mode=`) rather than in component state, so
 * a reload or a shared link lands on the same screen the customer was on.
 */

import { ArrowRightIcon, GlobeIcon, SlidersIcon } from "@/components/ui/icons";
import { Card } from "@/components/ui/Card";

export type SetupMode = "manual" | "website";

export function SetupChoice({ onChoose }: { onChoose: (mode: SetupMode) => void }) {
  return (
    <div className="mx-auto max-w-3xl">
      <div className="text-center">
        <p className="eyebrow">Get started</p>
        <h1 className="heading-xl mt-2">How would you like to create your AI receptionist?</h1>
        <p className="lead mx-auto mt-3 max-w-xl">
          Either way you can change everything afterwards, and nothing goes live until you say so.
        </p>
      </div>

      <div className="mt-8 grid gap-4 sm:mt-10 sm:grid-cols-2">
        <Option
          icon={<GlobeIcon size={20} />}
          title="Use my website"
          description="Enter your website and we'll read it, then show you what your receptionist learned before anything goes live."
          detail="Takes about a minute"
          onChoose={() => onChoose("website")}
        />
        <Option
          icon={<SlidersIcon size={20} />}
          title="Set up manually"
          description="Enter your business details and configure your receptionist yourself, step by step."
          detail="Takes about five minutes"
          onChoose={() => onChoose("manual")}
        />
      </div>
    </div>
  );
}

function Option({
  icon,
  title,
  description,
  detail,
  onChoose,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  detail: string;
  onChoose: () => void;
}) {
  return (
    <Card interactive className="overflow-hidden">
      {/* A button, not a link: there is no page to deep-link to until a mode is
          chosen, and a real button gets keyboard and screen-reader behaviour free. */}
      <button
        type="button"
        onClick={onChoose}
        className="flex h-full w-full flex-col items-start gap-3 p-5 text-left sm:p-6"
      >
        <span className="flex size-10 items-center justify-center rounded-xl bg-accent-soft text-accent-ink">
          {icon}
        </span>
        <span className="text-base font-semibold text-ink">{title}</span>
        <span className="text-sm leading-relaxed text-muted">{description}</span>
        <span className="mt-auto flex items-center gap-1.5 pt-2 text-sm font-semibold text-accent">
          {detail}
          <ArrowRightIcon size={15} />
        </span>
      </button>
    </Card>
  );
}
