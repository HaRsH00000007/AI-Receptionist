import { ButtonLink } from "@/components/ui/Button";
import { ArrowRightIcon } from "@/components/ui/icons";

import { TALK_TO_US_HREF } from "./links";

export function FinalCta() {
  return (
    <section aria-labelledby="cta-title" className="px-4 pb-20 sm:px-6 sm:pb-28">
      <div className="relative mx-auto max-w-6xl overflow-hidden rounded-[24px] bg-night px-6 py-16 text-center sm:px-12 sm:py-20">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 -top-40 mx-auto h-80 max-w-2xl rounded-full bg-[#3651f0]/25 blur-3xl"
        />
        <div className="relative">
          <h2 id="cta-title" className="heading-xl mx-auto max-w-2xl text-night-ink">
            Give every caller a better first impression.
          </h2>
          <p className="mx-auto mt-5 max-w-xl text-[1.0625rem] leading-relaxed text-night-muted">
            Set up your AI receptionist today and let it answer the calls you can’t get to.
          </p>
          <div className="mt-9 flex flex-col justify-center gap-3 sm:flex-row">
            <ButtonLink href="/get-started" size="lg" variant="inverse" trailingIcon={<ArrowRightIcon size={17} />}>
              Get started
            </ButtonLink>
            <ButtonLink href={TALK_TO_US_HREF} size="lg" variant="inverse-ghost">
              Talk to us
            </ButtonLink>
          </div>
        </div>
      </div>
    </section>
  );
}
