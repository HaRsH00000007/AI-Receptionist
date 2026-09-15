import { LiveCallDemo } from "./LiveCallDemo";
import { SectionHeading } from "./SectionHeading";

export function LiveCallSection() {
  return (
    <section aria-labelledby="live-call-title" className="bg-night py-20 text-night-ink sm:py-28">
      <div className="container-page">
        <SectionHeading
          id="live-call-title"
          inverse
          eyebrow="See it in action"
          title="Real conversations, handled end to end"
          description="Pick an example call. The receptionist talks with the caller while the details you need are captured alongside."
        />
        <LiveCallDemo />
        <p className="mt-5 text-center text-xs text-night-muted">
          Example conversations for illustration. Names and numbers are fictional.
        </p>
      </div>
    </section>
  );
}
