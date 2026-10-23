"use client";

/**
 * Building a receptionist from a business website.
 *
 * Eight screens: basics, the website, the read itself, what it learned, what to
 * ask callers, voice, greeting, and the number — then create. The shape follows
 * the manual wizard deliberately (one numeric step, per-step validation, one
 * submit handler) so the two are maintained the same way.
 *
 * Three things are load-bearing and worth not "simplifying" later:
 *
 * **The job is the state.** Progress lives on the server, so closing the tab and
 * coming back resumes where the customer was — the wizard opens on the step
 * that matches the job's status rather than at the beginning.
 *
 * **Nothing is created until the last click.** Every screen before it saves a
 * draft. The number is not bought, the agent is not made, nothing is charged.
 * The final button is the only one that provisions, and it says so.
 *
 * **What is switched off is not sent.** A knowledge item or question toggled off
 * is excluded from the prompt the receptionist is built from — the backend
 * enforces it, and the copy here tells the customer that is what the toggle
 * means.
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Feedback";
import { ChoiceGroup, SelectField, TextAreaField, TextField } from "@/components/ui/Field";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  CheckIcon,
  ChevronDownIcon,
  GlobeIcon,
  PlusIcon,
  SearchIcon,
} from "@/components/ui/icons";
import { Spinner } from "@/components/ui/Spinner";
import {
  ApiError,
  confirmWebsiteSetup,
  discardWebsiteSetup,
  getWebsiteSetup,
  listWebsiteVoices,
  retryWebsiteSetup,
  searchAvailableNumbers,
  startWebsiteSetup,
} from "@/lib/api";
import { formatPhone } from "@/lib/format";
import { GREETING_MAX_LENGTH } from "@/lib/greetings";
import {
  BUSINESS_TYPES,
  GREETING_STYLES,
  type BusinessType,
  type CallerQuestionView,
  type GreetingStyle,
  type KnowledgeItemView,
  type NumberSearchView,
  type SmsTemplateView,
  type VoiceOptionView,
  type WebsiteKnowledgeView,
  type WebsiteSetupView,
} from "@/lib/types";

type Step = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7;
const LAST_STEP: Step = 7;

const STEPS: Record<Step, { label: string; title: string; description?: string }> = {
  0: {
    label: "Basics",
    title: "First, a few basics",
    description: "You'll be notified after each call. Nothing goes live until you're ready.",
  },
  1: {
    label: "Website",
    title: "Teach your agent about your business",
    description: "We'll read your public website. It needs to be reachable without a login.",
  },
  2: { label: "Reading", title: "Teaching your agent the basics…" },
  3: {
    label: "Knowledge",
    title: "Here's what your agent learned",
    description: "Edit anything that's wrong, and switch off anything it shouldn't say.",
  },
  4: {
    label: "Questions",
    title: "What info do you want to ask callers for?",
    description: "Your receptionist asks for these during the call.",
  },
  5: { label: "Voice", title: "Select a voice for your agent", description: "You can change this later." },
  6: { label: "Greeting", title: "How should your agent introduce itself?" },
  7: { label: "Number", title: "Last step — pick a number" },
};

/**
 * Where Back goes from each step.
 *
 * Not `step - 1`: going back from the review would land on the "reading your
 * website" screen, which has nothing left to report for a finished crawl and
 * would sit there saying "getting ready" forever. Back from the review belongs
 * on the website field, where the customer can point us somewhere else.
 */
const PREVIOUS: Record<Step, Step | "leave"> = {
  0: "leave",
  1: 0,
  2: 1,
  3: 1,
  4: 3,
  5: 4,
  6: 5,
  7: 6,
};

/** Where a job's status says the customer belongs. */
function stepForStatus(status: WebsiteSetupView["status"]): Step {
  if (status === "pending" || status === "crawling" || status === "generating") return 2;
  // "confirmed" means this draft already built a business. There is nothing to
  // resume — reopening its review screen would offer edits to a receptionist
  // that may since have been deleted. Start from the beginning instead.
  if (status === "confirmed") return 0;
  return 3;
}

const POLL_MS = 2000;

export function WebsiteWizard({
  accountEmail,
  accountName,
  existing,
  onSwitchToManual,
  onLeave,
}: {
  accountEmail: string;
  accountName: string;
  existing: WebsiteSetupView | null;
  onSwitchToManual: () => void;
  /** Back from the first step: out of this flow, to the choice of both. */
  onLeave: () => void;
}) {
  const router = useRouter();

  // A finished draft is history, not state to pick up.
  const resumable = existing && existing.status !== "confirmed" ? existing : null;
  const [job, setJob] = useState<WebsiteSetupView | null>(resumable);
  const [step, setStep] = useState<Step>(() => (resumable ? stepForStatus(resumable.status) : 0));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  // Basics
  const [fullName, setFullName] = useState(accountName);
  const [email, setEmail] = useState(accountEmail);
  const [phone, setPhone] = useState("");
  const [heardAbout, setHeardAbout] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState(resumable?.website_url ?? "");

  // The draft
  const [knowledge, setKnowledge] = useState<WebsiteKnowledgeView | null>(
    resumable?.knowledge ?? null,
  );
  const [businessType, setBusinessType] = useState<BusinessType>("other");
  const [greetingStyle, setGreetingStyle] = useState<GreetingStyle>("professional");
  const [greeting, setGreeting] = useState("");
  const [recordingAck, setRecordingAck] = useState(false);
  const [voiceId, setVoiceId] = useState("");
  const [voices, setVoices] = useState<VoiceOptionView[]>([]);
  const [areaCode, setAreaCode] = useState("");
  const [numbers, setNumbers] = useState<NumberSearchView | null>(null);
  const [selectedNumber, setSelectedNumber] = useState("");
  const [searching, setSearching] = useState(false);

  const headingRef = useRef<HTMLHeadingElement>(null);
  const mounted = useRef(false);

  useEffect(() => {
    if (mounted.current) headingRef.current?.focus();
    mounted.current = true;
  }, [step]);

  // Poll while the job is running. Stops as soon as it is ready or failed, so a
  // customer watching the "reading your website" screen sees it change by
  // itself rather than having to reload.
  const status = job?.status;
  useEffect(() => {
    if (status !== "pending" && status !== "crawling" && status !== "generating") return;
    let cancelled = false;
    const timer = setInterval(() => {
      void (async () => {
        try {
          const fresh = await getWebsiteSetup();
          if (cancelled) return;
          setJob(fresh);
          if (fresh.status === "ready") {
            setKnowledge(fresh.knowledge);
            if (fresh.knowledge?.business_name) {
              setGreeting(
                `Hello, thank you for calling ${fresh.knowledge.business_name}. How can I help you today?`,
              );
            }
            setStep(3);
          }
        } catch {
          // A dropped poll is not worth a message: the next tick retries.
        }
      })();
    }, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [status]);

  useEffect(() => {
    if (step !== 5 || voices.length) return;
    void (async () => {
      try {
        const options = await listWebsiteVoices();
        setVoices(options);
        setVoiceId((current) => current || options[0]?.voice_id || "");
      } catch {
        setVoices([]);
      }
    })();
  }, [step, voices.length]);

  const fail = useCallback((caught: unknown, fallback: string) => {
    const failure = caught instanceof ApiError ? caught : null;
    setError(failure?.message ?? fallback);
    setFieldErrors(failure?.fieldErrors ?? {});
  }, []);

  // ---- actions ---------------------------------------------------------

  async function begin() {
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      // The address, and nothing else. The basics screen stays in the browser
      // with the rest of them until the create button; the crawl only ever
      // needed somewhere to read.
      const started = await startWebsiteSetup({ website_url: websiteUrl });
      setJob(started);
      setStep(2);
    } catch (caught) {
      fail(caught, "We couldn't start reading that website.");
    } finally {
      setBusy(false);
    }
  }

  async function retry() {
    setBusy(true);
    try {
      setJob(await retryWebsiteSetup());
      setStep(2);
    } catch (caught) {
      fail(caught, "We couldn't try that again.");
    } finally {
      setBusy(false);
    }
  }

  async function findNumbers() {
    if (!/^\d{3}$/.test(areaCode)) {
      setFieldErrors({ area_code: "Enter a three-digit area code." });
      return;
    }
    setSearching(true);
    setFieldErrors({});
    try {
      const found = await searchAvailableNumbers(areaCode);
      setNumbers(found);
      setSelectedNumber(found.numbers[0]?.e164 ?? "");
    } catch (caught) {
      fail(caught, "We couldn't load available numbers.");
    } finally {
      setSearching(false);
    }
  }

  /**
   * The only write in the whole flow, apart from the crawl itself.
   *
   * Every screen is sent here together, which is what makes abandoning the
   * wizard cost nothing: until this button, the review, the questions, the
   * voice and the greeting exist only in this component.
   */
  async function create() {
    if (!knowledge) return;
    setBusy(true);
    setError(null);
    try {
      await confirmWebsiteSetup({
        knowledge: {
          business_name: knowledge.business_name,
          business_summary: knowledge.business_summary,
          operating_hours: knowledge.operating_hours,
          services: knowledge.services,
          items: knowledge.items,
          questions: knowledge.questions,
          sms_templates: knowledge.sms_templates,
        },
        choices: {
          business_type: businessType,
          greeting_style: greetingStyle,
          custom_greeting: greeting,
          voice_id: voiceId,
          language: "en",
          recording_acknowledged: recordingAck,
          area_code: areaCode,
          selected_number: selectedNumber,
          contact_phone: phone,
          notification_email: email,
          full_name: fullName,
          heard_about: heardAbout,
        },
      });
      router.push("/dashboard/setup");
    } catch (caught) {
      fail(caught, "We couldn't create your receptionist.");
    } finally {
      setBusy(false);
    }
  }

  /** Leaving for the manual form. Same rule as Back: nothing is left behind. */
  async function switchToManual() {
    try {
      await discardWebsiteSetup();
    } catch {
      // They are leaving either way.
    }
    setJob(null);
    onSwitchToManual();
  }

  async function back() {
    const previous = PREVIOUS[step];
    if (previous !== "leave") {
      setStep(previous);
      return;
    }
    // Out of the flow entirely, so nothing of this attempt is left behind: the
    // crawl is the one thing that was written, and it goes too. Reported but
    // never blocking — a discard that fails must not trap someone in a wizard
    // they are trying to leave.
    try {
      await discardWebsiteSetup();
    } catch {
      // Nothing to tell the customer: they asked to leave, and they are leaving.
    }
    setJob(null);
    onLeave();
  }

  async function next(event: FormEvent) {
    event.preventDefault();
    if (busy) return;

    if (step === 0) {
      const found: Record<string, string> = {};
      if (!fullName.trim()) found.full_name = "Tell us your name.";
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) found.notification_email = "Enter a valid email.";
      if (phone.replace(/\D/g, "").length < 7) found.contact_phone = "Enter a phone number.";
      setFieldErrors(found);
      if (Object.keys(found).length) return;
      setStep(1);
      return;
    }
    if (step === 1) {
      if (!websiteUrl.trim()) {
        setFieldErrors({ website_url: "Enter your website address." });
        return;
      }
      await begin();
      return;
    }
    // Steps 3 to 6 move on without touching the server. Everything the customer
    // decides is held here and sent once, by `create`.
    if (step === 3 && knowledge) {
      setStep(4);
      return;
    }
    if (step === 4 && knowledge) {
      setStep(5);
      return;
    }
    if (step === 5) {
      setStep(6);
      return;
    }
    if (step === 6) {
      if (!greeting.trim()) {
        setFieldErrors({ custom_greeting: "Your receptionist needs an opening line." });
        return;
      }
      setStep(7);
      return;
    }
    if (step === LAST_STEP) {
      await create();
    }
  }

  const meta = STEPS[step];
  const failed = job?.status === "failed";

  return (
    <div className="mx-auto max-w-3xl">
      <Progress step={step} />

      <form className="mt-6" noValidate onSubmit={next}>
        <div className="text-center">
          <h1 ref={headingRef} tabIndex={-1} className="heading-lg outline-none">
            {meta.title}
          </h1>
          {meta.description && <p className="lead mx-auto mt-2 max-w-xl">{meta.description}</p>}
        </div>

        {error && (
          <Alert tone="danger" title="That didn't work" className="mt-6">
            {error}
          </Alert>
        )}

        <div key={step} className="animate-fade-in mt-6 space-y-5">
          {step === 0 && (
            <Card className="p-5 sm:p-6">
              <div className="space-y-5">
                <TextField
                  label="Full name"
                  value={fullName}
                  onChange={(event) => setFullName(event.target.value)}
                  error={fieldErrors.full_name}
                  placeholder="Dana Rivera"
                />
                <TextField
                  label="Email"
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  error={fieldErrors.notification_email}
                  hint="Where your call summaries go."
                />
                <TextField
                  label="Phone number"
                  value={phone}
                  onChange={(event) => setPhone(event.target.value)}
                  error={fieldErrors.contact_phone}
                  placeholder="(555) 555-5555"
                  hint="We won't answer your calls until you forward them to us."
                />
                <TextField
                  label="How did you hear about us?"
                  optional
                  value={heardAbout}
                  onChange={(event) => setHeardAbout(event.target.value)}
                  placeholder="Google, a friend, a fortune cookie"
                />
              </div>
            </Card>
          )}

          {step === 1 && (
            <Card className="p-5 sm:p-6">
              <div className="grid grid-cols-2 gap-2 rounded-xl bg-surface-2 p-1">
                <span className="rounded-lg bg-surface px-4 py-2 text-center text-sm font-semibold text-ink shadow-xs">
                  Website
                </span>
                <button
                  type="button"
                  onClick={() => void switchToManual()}
                  className="rounded-lg px-4 py-2 text-center text-sm font-medium text-muted hover:text-ink"
                >
                  Set up manually
                </button>
              </div>
              <div className="mt-5 space-y-4">
                <TextField
                  label="Your website"
                  value={websiteUrl}
                  onChange={(event) => setWebsiteUrl(event.target.value)}
                  error={fieldErrors.website_url}
                  placeholder="https://yourwebsite.com"
                  hint="A public page we can read without signing in. We follow your robots.txt."
                />
                <p className="text-xs text-muted">
                  We read a handful of pages — about, services, pricing, contact — and show you
                  everything before it goes anywhere near a caller.
                </p>
              </div>
            </Card>
          )}

          {step === 2 && (
            <Reading job={job} failed={failed} busy={busy} onRetry={retry} onManual={() => void switchToManual()} />
          )}

          {step === 3 && knowledge && (
            <KnowledgeReview knowledge={knowledge} crawl={job?.crawl ?? null} onChange={setKnowledge} />
          )}

          {step === 4 && knowledge && (
            <Questions knowledge={knowledge} onChange={setKnowledge} />
          )}

          {step === 5 && (
            <Voices voices={voices} voiceId={voiceId} onPick={setVoiceId} />
          )}

          {step === 6 && (
            <Card className="p-5 sm:p-6">
              <div className="space-y-5">
                <ChoiceGroup<BusinessType>
                  legend="Business type"
                  name="business_type"
                  value={businessType}
                  onChange={setBusinessType}
                  options={BUSINESS_TYPES.map((option) => ({
                    value: option.value,
                    label: option.label,
                  }))}
                  columns={3}
                />
                <ChoiceGroup<GreetingStyle>
                  legend="Tone"
                  name="greeting_style"
                  value={greetingStyle}
                  onChange={setGreetingStyle}
                  options={GREETING_STYLES.map((option) => ({
                    value: option.value,
                    label: option.label,
                  }))}
                  columns={3}
                />
                <TextAreaField
                  label="Greeting message"
                  rows={3}
                  maxLength={GREETING_MAX_LENGTH}
                  value={greeting}
                  onChange={(event) => setGreeting(event.target.value)}
                  error={fieldErrors.custom_greeting}
                  hint="Callers hear this word for word."
                />
                <label className="flex cursor-pointer gap-3 rounded-xl border border-line p-3.5 text-sm">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4 shrink-0 accent-accent"
                    checked={recordingAck}
                    onChange={(event) => setRecordingAck(event.target.checked)}
                  />
                  <span className="text-muted">
                    I understand calls may be recorded and transcribed, and that notifying callers
                    where the law requires it is my responsibility.
                  </span>
                </label>
              </div>
            </Card>
          )}

          {step === 7 && (
            <NumberPicker
              areaCode={areaCode}
              onAreaCode={(value) => {
                setAreaCode(value);
                setNumbers(null);
                setSelectedNumber("");
              }}
              error={fieldErrors.area_code}
              searching={searching}
              numbers={numbers}
              selected={selectedNumber}
              onSelect={setSelectedNumber}
              onSearch={findNumbers}
              summary={{
                websiteUrl: job?.website_url ?? websiteUrl,
                pages: job?.crawl?.pages_read ?? 0,
                items: (knowledge?.items ?? []).filter((item) => item.enabled).length,
                questions: (knowledge?.questions ?? []).filter((item) => item.enabled).length,
                greeting,
                voice: voices.find((option) => option.voice_id === voiceId)?.label ?? "Default",
              }}
            />
          )}
        </div>

        <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
          <Button
            variant="secondary"
            leadingIcon={<ArrowLeftIcon size={16} />}
            onClick={() => void back()}
            disabled={busy}
          >
            Back
          </Button>
          {/* No Next while the website is being read: the screen advances by
              itself when the job finishes. Back still works, so nobody is
              stuck watching a crawl they have changed their mind about. */}
          {step !== 2 && (
            <Button
              type="submit"
              loading={busy}
              trailingIcon={step === LAST_STEP ? undefined : <ArrowRightIcon size={16} />}
            >
              {step === LAST_STEP ? "Create my receptionist" : step === 1 ? "Train agent" : "Next"}
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Steps
// ---------------------------------------------------------------------------

function Progress({ step }: { step: Step }) {
  const total = LAST_STEP + 1;
  return (
    <div>
      <div className="flex items-center gap-1.5">
        {Array.from({ length: total }, (_, index) => (
          <span
            key={index}
            className={
              index <= step
                ? "h-1.5 flex-1 rounded-full bg-accent"
                : "h-1.5 flex-1 rounded-full bg-line"
            }
          />
        ))}
      </div>
      <p className="mt-2 text-sm text-muted">
        <span className="tabular-nums">
          {step + 1} of {total}
        </span>{" "}
        <span className="font-semibold text-ink">{STEPS[step].label}</span>
      </p>
    </div>
  );
}

function Reading({
  job,
  failed,
  busy,
  onRetry,
  onManual,
}: {
  job: WebsiteSetupView | null;
  failed: boolean;
  busy: boolean;
  onRetry: () => void;
  onManual: () => void;
}) {
  if (failed) {
    return (
      <Card className="p-6">
        <Alert tone="warn" title="We couldn't finish reading your website">
          {job?.last_error ?? "Something went wrong."}
        </Alert>
        <div className="mt-5 flex flex-wrap gap-2.5">
          <Button onClick={onRetry} loading={busy}>
            Try again
          </Button>
          <Button variant="secondary" onClick={onManual}>
            Set up manually instead
          </Button>
        </div>
      </Card>
    );
  }

  const stage =
    job?.status === "generating"
      ? "Making sense of what we read…"
      : job?.status === "crawling"
        ? "Reading your website…"
        : "Getting ready…";

  return (
    <Card className="p-10">
      <div className="flex flex-col items-center gap-5 text-center" aria-busy>
        <span className="animate-float flex size-20 items-center justify-center rounded-full bg-accent-soft">
          <span className="flex size-12 items-center justify-center rounded-full bg-accent text-accent-fg">
            <GlobeIcon size={22} />
          </span>
        </span>
        <p role="status" className="text-sm font-medium text-ink">
          {stage}
        </p>
        <p className="max-w-sm text-sm text-muted">
          This usually takes under a minute. You can close this tab &mdash; we&apos;ll keep your progress and
          you can pick it up from where you left off.
        </p>
        <Spinner className="size-4 text-accent" />
      </div>
    </Card>
  );
}

function KnowledgeReview({
  knowledge,
  crawl,
  onChange,
}: {
  knowledge: WebsiteKnowledgeView;
  crawl: WebsiteSetupView["crawl"];
  onChange: (next: WebsiteKnowledgeView) => void;
}) {
  const enabled = knowledge.items.filter((item) => item.enabled).length;

  return (
    <div className="space-y-4">
      {crawl && (
        <p className="text-center text-sm text-muted">
          Read {crawl.pages_read} {crawl.pages_read === 1 ? "page" : "pages"} of{" "}
          <span className="text-ink">{crawl.site}</span>
          {crawl.truncated && " (the largest pages only)"}.
        </p>
      )}

      {knowledge.items.length === 0 && (
        <Alert tone="warn" title="We couldn't find much on that website">
          Add what your receptionist should know below, or go back and set up manually.
        </Alert>
      )}

      <Section
        title="Knowledge"
        count={`${enabled} of ${knowledge.items.length} on`}
        description="Info your agent can share with callers"
      >
        {knowledge.items.map((item, index) => (
          <ItemRow
            key={item.id || index}
            item={item}
            onChange={(next) =>
              onChange({
                ...knowledge,
                items: knowledge.items.map((current, at) => (at === index ? next : current)),
              })
            }
            onRemove={() =>
              onChange({
                ...knowledge,
                items: knowledge.items.filter((_, at) => at !== index),
              })
            }
          />
        ))}
        <AddRow
          label="Add something your agent should know"
          onAdd={() =>
            onChange({
              ...knowledge,
              items: [
                ...knowledge.items,
                {
                  id: `custom-${knowledge.items.length + 1}`,
                  category: "business_description",
                  title: "New note",
                  content: "",
                  source_url: "",
                  needs_review: false,
                  enabled: true,
                },
              ],
            })
          }
        />
      </Section>

      <Section
        title="Text messages"
        count={`${knowledge.sms_templates.length} drafted`}
        description="Messages your agent could text a caller"
      >
        <Alert tone="info" className="mb-3">
          Texting isn&apos;t switched on yet. These are saved with your receptionist and will be
          ready when SMS campaigns launch — nothing is sent today.
        </Alert>
        {knowledge.sms_templates.map((template, index) => (
          <TemplateRow
            key={template.key || index}
            template={template}
            onChange={(next) =>
              onChange({
                ...knowledge,
                sms_templates: knowledge.sms_templates.map((current, at) =>
                  at === index ? next : current,
                ),
              })
            }
          />
        ))}
        {knowledge.sms_templates.length === 0 && (
          <p className="px-1 py-2 text-sm text-muted">None suggested from your website.</p>
        )}
      </Section>
    </div>
  );
}

function Questions({
  knowledge,
  onChange,
}: {
  knowledge: WebsiteKnowledgeView;
  onChange: (next: WebsiteKnowledgeView) => void;
}) {
  function update(index: number, next: CallerQuestionView) {
    onChange({
      ...knowledge,
      questions: knowledge.questions.map((current, at) => (at === index ? next : current)),
    });
  }

  return (
    <Card>
      <ul className="divide-y divide-line">
        {knowledge.questions.map((question, index) => (
          <li key={question.key} className="flex items-start gap-4 px-5 py-4">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-ink">{question.label}</p>
              {question.builtin ? (
                <p className="mt-0.5 text-xs text-muted">Always available to your receptionist.</p>
              ) : (
                <TextField
                  label="When to ask"
                  className="mt-2"
                  value={question.prompt}
                  onChange={(event) => update(index, { ...question, prompt: event.target.value })}
                />
              )}
            </div>
            <Toggle
              label={`Ask for ${question.label}`}
              on={question.enabled}
              onToggle={(on) => update(index, { ...question, enabled: on })}
            />
          </li>
        ))}
      </ul>
      <div className="border-t border-line p-4">
        <AddRow
          label="Add a question"
          onAdd={() =>
            onChange({
              ...knowledge,
              questions: [
                ...knowledge.questions,
                {
                  key: `custom_${knowledge.questions.length + 1}`,
                  label: "New question",
                  prompt: "",
                  builtin: false,
                  enabled: true,
                },
              ],
            })
          }
        />
      </div>
    </Card>
  );
}

function Voices({
  voices,
  voiceId,
  onPick,
}: {
  voices: VoiceOptionView[];
  voiceId: string;
  onPick: (value: string) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="flex justify-center">
        <SelectField
          label="Language"
          value="en"
          onChange={() => undefined}
          options={[{ value: "en", label: "English" }]}
          hint="English is the only language your receptionist speaks today."
          className="w-48"
        />
      </div>
      {voices.length === 0 ? (
        <div className="grid gap-3 sm:grid-cols-3">
          <Skeleton className="h-28 rounded-xl" />
          <Skeleton className="h-28 rounded-xl" />
          <Skeleton className="h-28 rounded-xl" />
        </div>
      ) : (
        <ChoiceGroup
          legend="Voice"
          hideLegend
          name="voice_id"
          value={voiceId}
          onChange={onPick}
          columns={3}
          options={voices.map((voice) => ({
            value: voice.voice_id,
            label: voice.label,
            description: voice.style ? `${voice.style} tone` : "The configured default",
          }))}
        />
      )}
      <p className="text-center text-xs text-muted">
        These are the voices this workspace is set up for.
      </p>
    </div>
  );
}

function NumberPicker({
  areaCode,
  onAreaCode,
  error,
  searching,
  numbers,
  selected,
  onSelect,
  onSearch,
  summary,
}: {
  areaCode: string;
  onAreaCode: (value: string) => void;
  error?: string;
  searching: boolean;
  numbers: NumberSearchView | null;
  selected: string;
  onSelect: (value: string) => void;
  onSearch: () => void;
  summary: {
    websiteUrl: string;
    pages: number;
    items: number;
    questions: number;
    greeting: string;
    voice: string;
  };
}) {
  return (
    <div className="space-y-4">
      <Card className="p-5 sm:p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
          <TextField
            label="Area code"
            inputMode="numeric"
            maxLength={3}
            className="sm:w-40"
            value={areaCode}
            error={error}
            onChange={(event) => onAreaCode(event.target.value.replace(/\D/g, "").slice(0, 3))}
          />
          <Button
            variant="secondary"
            size="lg"
            className="sm:mt-8"
            leadingIcon={<SearchIcon size={16} />}
            loading={searching}
            onClick={onSearch}
          >
            {numbers ? "Search again" : "Find numbers"}
          </Button>
        </div>

        {numbers && numbers.numbers.length > 0 && (
          <div className="mt-5">
            {!numbers.exact_match && (
              <Alert tone="warn" title={`Nothing free in ${numbers.requested_area_code}`} className="mb-3">
                Here are the closest ones we could find.
              </Alert>
            )}
            <ChoiceGroup
              legend="Pick a number"
              name="selected_number"
              columns={2}
              value={selected}
              onChange={onSelect}
              options={numbers.numbers.map((number) => ({
                value: number.e164,
                label: formatPhone(number.e164),
                description: [number.locality, number.region].filter(Boolean).join(", ") || undefined,
              }))}
            />
          </div>
        )}
        {numbers && numbers.numbers.length === 0 && (
          <Alert tone="warn" title="No numbers are available there" className="mt-5">
            Try another area code — or continue, and setup will choose one for you.
          </Alert>
        )}
        {!numbers && (
          <p className="mt-4 text-sm text-muted">
            Skip this and setup picks a number for you. Nothing is bought until you create your
            receptionist.
          </p>
        )}
      </Card>

      <Card>
        <CardHeader title="Ready to create" description="Everything below is what your receptionist will start with." />
        <dl className="divide-y divide-line text-sm">
          <Fact label="Website">{summary.websiteUrl}</Fact>
          <Fact label="Pages read">{summary.pages}</Fact>
          <Fact label="Knowledge items">{summary.items}</Fact>
          <Fact label="Caller questions">{summary.questions}</Fact>
          <Fact label="Voice">{summary.voice}</Fact>
          <Fact label="Greeting">{summary.greeting || "Written for you"}</Fact>
          <Fact label="Number">{selected ? formatPhone(selected) : "Chosen during setup"}</Fact>
        </dl>
        <p className="border-t border-line bg-surface-2 px-5 py-3 text-xs text-muted">
          Creating your receptionist reserves a phone number on your plan. Nothing has been bought
          up to this point.
        </p>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small pieces
// ---------------------------------------------------------------------------

function Section({
  title,
  count,
  description,
  children,
}: {
  title: string;
  count: string;
  description: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <Card className="overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left"
      >
        <span className="min-w-0">
          <span className="flex items-center gap-2">
            <span className="text-sm font-semibold text-ink">{title}</span>
            <Badge>{count}</Badge>
          </span>
          <span className="mt-0.5 block text-sm text-muted">{description}</span>
        </span>
        <ChevronDownIcon
          size={18}
          className={open ? "shrink-0 rotate-180 text-muted" : "shrink-0 text-muted"}
        />
      </button>
      {open && <div className="border-t border-line p-4 sm:p-5">{children}</div>}
    </Card>
  );
}

function ItemRow({
  item,
  onChange,
  onRemove,
}: {
  item: KnowledgeItemView;
  onChange: (next: KnowledgeItemView) => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-line py-3 last:border-0">
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={() => setOpen((current) => !current)}
          className="min-w-0 flex-1 text-left"
          aria-expanded={open}
        >
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-ink underline decoration-line underline-offset-4">
              {item.title}
            </span>
            {item.needs_review && <Badge tone="warn">Check this</Badge>}
          </span>
          <span className="mt-1 block truncate text-sm text-muted">
            {item.content || "Nothing yet — tap to write it."}
          </span>
        </button>
        <Toggle
          label={`Use ${item.title}`}
          on={item.enabled}
          onToggle={(on) => onChange({ ...item, enabled: on })}
        />
      </div>
      {open && (
        <div className="mt-3 space-y-3">
          <TextField
            label="Title"
            value={item.title}
            onChange={(event) => onChange({ ...item, title: event.target.value })}
          />
          <TextAreaField
            label="What your receptionist can say"
            rows={4}
            maxLength={1200}
            value={item.content}
            onChange={(event) => onChange({ ...item, content: event.target.value })}
          />
          <div className="flex items-center justify-between gap-3">
            {item.source_url ? (
              <a href={item.source_url} target="_blank" rel="noreferrer" className="link text-xs">
                Found on your website
              </a>
            ) : (
              <span className="text-xs text-muted">Added by you</span>
            )}
            <Button variant="ghost" size="sm" onClick={onRemove}>
              Remove
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function TemplateRow({
  template,
  onChange,
}: {
  template: SmsTemplateView;
  onChange: (next: SmsTemplateView) => void;
}) {
  return (
    <div className="border-b border-line py-3 last:border-0">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-ink">{template.label}</p>
          <p className="mt-1 text-sm text-muted">{template.body}</p>
        </div>
        <Toggle
          label={`Keep ${template.label}`}
          on={template.enabled}
          onToggle={(on) => onChange({ ...template, enabled: on })}
        />
      </div>
    </div>
  );
}

function AddRow({ label, onAdd }: { label: string; onAdd: () => void }) {
  return (
    <Button variant="ghost" size="sm" leadingIcon={<PlusIcon size={15} />} onClick={onAdd}>
      {label}
    </Button>
  );
}

/** A switch. A real checkbox underneath, so it is keyboard- and AT-operable. */
function Toggle({
  label,
  on,
  onToggle,
}: {
  label: string;
  on: boolean;
  onToggle: (on: boolean) => void;
}) {
  return (
    <label className="relative inline-flex shrink-0 cursor-pointer items-center">
      <input
        type="checkbox"
        className="peer sr-only"
        checked={on}
        aria-label={label}
        onChange={(event) => onToggle(event.target.checked)}
      />
      <span
        className={
          on
            ? "flex h-6 w-10 items-center rounded-full bg-accent px-0.5 transition-colors peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent"
            : "flex h-6 w-10 items-center rounded-full bg-line px-0.5 transition-colors peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent"
        }
      >
        <span
          className={
            on
              ? "flex size-5 translate-x-4 items-center justify-center rounded-full bg-surface text-accent transition-transform"
              : "size-5 rounded-full bg-surface transition-transform"
          }
        >
          {on && <CheckIcon size={12} />}
        </span>
      </span>
    </label>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 px-5 py-3">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 text-right font-medium break-words text-ink">{children}</dd>
    </div>
  );
}
