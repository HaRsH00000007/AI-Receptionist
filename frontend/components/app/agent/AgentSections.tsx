"use client";

/**
 * The Agent area: a settings list down the left, one panel on the right.
 *
 * Sections are addressed by `?section=`, not by route, because they are panels
 * of one page rather than separate destinations — but they stay linkable, so a
 * link can still open the one that matters. The nav is a real list of links
 * for the same reason: middle-click and "open in new tab" work.
 *
 * Every section in the reference design is listed, including the ones that are
 * not built. See `sections.ts` for why.
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { AgentGeneral } from "@/components/app/agent/AgentGeneral";
import { AgentGreetings } from "@/components/app/agent/AgentGreetings";
import { AgentTags } from "@/components/app/agent/AgentTags";
import { AgentTransfers } from "@/components/app/agent/AgentTransfers";
import { AGENT_SECTIONS, sectionFor, type AgentSection } from "@/components/app/agent/sections";
import { useTenantData } from "@/components/app/TenantWorkspace";
import { AgentView } from "@/components/app/views/AgentView";
import { Alert } from "@/components/ui/Alert";
import { ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { cx } from "@/components/ui/cx";
import { PageHeader } from "@/components/ui/PageHeader";
import { formatPhone } from "@/lib/format";

function NotBuilt({ section }: { section: AgentSection }) {
  const Icon = section.icon;
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Icon size={18} className="text-muted" />
            {section.label}
          </span>
        }
        description="Not built yet."
      />
      <div className="space-y-4 p-4 pt-0">
        <p className="text-sm text-muted">{section.summary}</p>
        <Alert tone="info">
          Nothing here is saved or applied yet. This section is listed so you can see what&apos;s
          planned, not because it does anything.
        </Alert>
        {section.elsewhere ? (
          <div className="space-y-2">
            <p className="text-sm text-muted">What exists today:</p>
            <ButtonLink href={section.elsewhere.href} variant="secondary">
              {section.elsewhere.label}
            </ButtonLink>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function Panel({ section }: { section: AgentSection }) {
  if (!section.built) return <NotBuilt section={section} />;
  if (section.id === "general") {
    // Two components, one panel, and siblings rather than nested: the settings
    // above are a single form with one save, while each scheduled greeting
    // saves on its own — and a form inside a form is not valid HTML.
    return (
      <div className="space-y-6">
        <AgentGeneral />
        <AgentGreetings />
      </div>
    );
  }
  if (section.id === "transfer") return <AgentTransfers />;
  if (section.id === "tags") return <AgentTags />;
  // Training is the existing agent page: what the receptionist knows, the
  // conversation preview, version history and the danger zone. Reused whole
  // rather than reimplemented beside it.
  return <AgentView embedded />;
}

export function AgentSections() {
  const { phone } = useTenantData();
  const params = useSearchParams();
  const active = sectionFor(params?.get("section"));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Agent"
        description="Your AI receptionist: what it knows, and how it answers."
        actions={
          phone ? (
            <ButtonLink href={`tel:${phone.e164}`} variant="secondary">
              Call your agent at {formatPhone(phone.e164)}
            </ButtonLink>
          ) : undefined
        }
      />
      <div className="flex flex-col gap-6 lg:flex-row">
        <nav aria-label="Agent settings" className="lg:w-56 lg:shrink-0">
          <ul className="-mx-4 flex gap-1 overflow-x-auto px-4 lg:mx-0 lg:flex-col lg:px-0">
            {AGENT_SECTIONS.map((section) => {
              const current = section.id === active.id;
              const Icon = section.icon;
              return (
                <li key={section.id} className="shrink-0">
                  <Link
                    href={`/dashboard/agent?section=${section.id}`}
                    aria-current={current ? "page" : undefined}
                    className={cx(
                      "flex items-center gap-2.5 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                      current
                        ? "bg-accent-soft text-ink"
                        : "text-muted hover:bg-surface-2 hover:text-ink",
                    )}
                  >
                    <Icon
                      size={16}
                      className={cx(
                        "shrink-0 transition-colors",
                        // Dimmer than the label when the section is only a
                        // placeholder, so the nav reads at a glance.
                        current ? "text-accent" : section.built ? "" : "opacity-60",
                      )}
                    />
                    <span>{section.label}</span>
                    {!section.built ? (
                      <span className="ml-auto pl-2 text-xs font-normal text-muted">Soon</span>
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="min-w-0 flex-1">
          <Panel section={active} />
        </div>
      </div>
    </div>
  );
}
