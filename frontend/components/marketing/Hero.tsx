import { ButtonLink } from "@/components/ui/Button";
import { ArrowRightIcon, CheckIcon } from "@/components/ui/icons";

import { HeroCallMockup } from "./HeroCallMockup";

const POINTS = [
  "A new local number, or keep your own",
  "Guided setup, step by step",
  "A summary of every call",
] as const;

export function Hero() {
  return (
    <section aria-labelledby="hero-title" className="relative overflow-hidden">
      <div aria-hidden className="grid-backdrop pointer-events-none absolute inset-x-0 top-0 h-[42rem]" />
      <div className="container-page relative grid items-center gap-14 pt-10 pb-20 sm:pt-16 lg:grid-cols-[1.05fr_1fr] lg:gap-12 lg:pt-20 lg:pb-28">
        <div className="animate-fade-up">
          <p className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-xs font-semibold text-ink-2 shadow-xs">
            <span className="size-1.5 rounded-full bg-success" aria-hidden />
            AI receptionist · answers 24/7
          </p>
          <h1 id="hero-title" className="display mt-6 max-w-[14ch]">
            Never miss another customer call.
          </h1>
          <p className="lead mt-6 max-w-xl">
            Your AI receptionist answers every call in your business’s voice, answers common
            questions, captures leads and takes messages — then sends you a clear summary of each
            conversation, day or night.
          </p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <ButtonLink href="/get-started" size="lg" trailingIcon={<ArrowRightIcon size={17} />}>
              Get started
            </ButtonLink>
            <ButtonLink href="#how-it-works" size="lg" variant="secondary">
              See how it works
            </ButtonLink>
          </div>
          <ul className="mt-8 flex flex-col gap-2.5 text-sm text-muted sm:flex-row sm:flex-wrap sm:gap-x-6">
            {POINTS.map((point) => (
              <li key={point} className="flex items-center gap-2">
                <CheckIcon size={16} className="text-success" />
                {point}
              </li>
            ))}
          </ul>
        </div>

        <div className="animate-fade-up min-w-0 [animation-delay:150ms]">
          <HeroCallMockup />
        </div>
      </div>
    </section>
  );
}
