"use client";

/**
 * Every receptionist this account has, as a gallery.
 *
 * The rest of the dashboard is per-business: pick a workspace, then look at it.
 * That makes "which of my receptionists is live and taking calls?" a question
 * you answer by switching workspace four times, and the switcher could only
 * ever show a name. This page answers it on one screen.
 *
 * Three things it is careful about:
 *
 * **"Live" is not "active".** A business can be paid up and still mid setup,
 * with no number and nothing answering. The badge reports what is actually
 * happening to a caller, and the card says what is missing when nothing is.
 *
 * **A cancelled business is still shown.** The account owns its history. A
 * gallery that silently dropped one would leave an owner hunting for it; its
 * card says what state it is in and offers nothing that would act on it.
 *
 * **Delete is a link, not a button.** Removing a receptionist can release a
 * phone number, which is irreversible and costs money to undo. That flow
 * already exists, with its own confirmation and its own choice about the
 * number; a second, lighter one next to an avatar is how somebody deletes the
 * wrong business. The quick action opens the real thing.
 */

import { useRouter } from "next/navigation";

import { useWorkspace } from "@/components/app/TenantWorkspace";
import { useLoad } from "@/components/app/useLoad";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Feedback";
import { AgentAvatarImage, avatarFor } from "@/components/ui/AgentAvatar";
import { HeadsetIcon, PhoneIcon, PlusIcon } from "@/components/ui/icons";
import { Menu } from "@/components/ui/Menu";
import { PageHeader } from "@/components/ui/PageHeader";
import { listReceptionists } from "@/lib/api";
import { formatPhone } from "@/lib/format";
import type { Tone } from "@/lib/tone";
import { NEW_BUSINESS_PATH } from "@/lib/routing";
import type { ReceptionistCardView } from "@/lib/types";

/**
 * What a caller would actually experience, in one word.
 *
 * The five tenant states this product has are `pending`, `active`, `failed`,
 * `abandoned` and `cancelled` — and `active` is the one that needs qualifying,
 * because a business can be active with nothing answering yet. A badge reading
 * "Active" over a receptionist nobody can reach is the exact thing this page
 * exists to stop.
 */
function state(card: ReceptionistCardView): { label: string; tone: Tone } {
  if (card.status === "cancelled" || card.status === "abandoned") {
    return { label: "Deleted", tone: "neutral" };
  }
  if (card.status === "failed") return { label: "Setup failed", tone: "danger" };
  if (card.live && card.phone_e164) return { label: "Live", tone: "success" };
  if (card.status === "active") return { label: "Not answering", tone: "warn" };
  return { label: "Setting up", tone: "warn" };
}

export function ReceptionistsView() {
  const { session, switchTenant } = useWorkspace();
  const router = useRouter();
  const listing = useLoad(() => listReceptionists(), `receptionists:${session.user_id}`);
  const { data, error, loading } = listing;

  /**
   * Open one of them.
   *
   * The workspace has to move first: every page under here reads one tenant
   * from context, so navigating without switching would open this card's
   * Agent page showing the *previous* business's receptionist.
   */
  function open(card: ReceptionistCardView, path: string) {
    switchTenant(card.tenant_id);
    router.push(path);
  }

  if (loading && !data) {
    return (
      <div className="space-y-6">
        <PageHeader title="Receptionists" description="Every receptionist on this account." />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <Skeleton className="h-44 rounded-xl" />
          <Skeleton className="h-44 rounded-xl" />
          <Skeleton className="h-44 rounded-xl" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        <PageHeader title="Receptionists" />
        <Alert
          tone="danger"
          title="Your receptionists couldn't be loaded"
          action={
            <Button size="sm" variant="secondary" onClick={() => listing.reload()}>
              Try again
            </Button>
          }
        >
          {error}
        </Alert>
      </div>
    );
  }

  const cards = data ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Receptionists"
        description="Every receptionist on this account, and what each one is doing."
        actions={
          <ButtonLink href={NEW_BUSINESS_PATH} leadingIcon={<PlusIcon size={16} />}>
            Add a receptionist
          </ButtonLink>
        }
      />

      {cards.length === 0 ? (
        <Card>
          <EmptyState
            className="border-0"
            icon={<HeadsetIcon size={22} />}
            title="No receptionists yet"
            description="Set one up and it will appear here."
            action={<ButtonLink href={NEW_BUSINESS_PATH}>Set up a receptionist</ButtonLink>}
          />
        </Card>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {cards.map((card) => {
            const badge = state(card);
            const gone = badge.label === "Deleted";
            return (
              <li key={card.tenant_id}>
                <Card className="flex h-full flex-col gap-4 p-5">
                  <div className="flex items-start gap-3">
                    <AgentAvatarImage
                      avatar={avatarFor(card.avatar, card.tenant_id)}
                      size={48}
                      className={gone ? "opacity-50" : undefined}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="truncate font-semibold text-ink">{card.name}</h2>
                        <Badge tone={badge.tone}>{badge.label}</Badge>
                      </div>
                      {card.agent_name ? (
                        <p className="truncate text-sm text-muted">{card.agent_name}</p>
                      ) : null}
                    </div>
                    {gone ? null : (
                      <Menu
                        label={`Actions for ${card.name}`}
                        iconOnly
                        trigger="⋯"
                        width="w-56"
                        items={[
                          {
                            label: "Edit receptionist",
                            onSelect: () => open(card, "/dashboard/agent"),
                          },
                          {
                            label: "See its calls",
                            onSelect: () => open(card, "/dashboard/calls"),
                          },
                          {
                            label: "Delete receptionist",
                            description: "Opens the full flow, including the number.",
                            tone: "danger",
                            onSelect: () => open(card, "/dashboard/settings/receptionist"),
                          },
                        ]}
                      />
                    )}
                  </div>

                  <dl className="space-y-1.5 text-sm">
                    <div className="flex items-center gap-2 text-muted">
                      <PhoneIcon size={15} className="shrink-0 text-subtle" />
                      <dt className="sr-only">Number</dt>
                      <dd>{card.phone_e164 ? formatPhone(card.phone_e164) : "No number yet"}</dd>
                    </div>
                    <div className="flex items-center gap-2 text-muted">
                      <HeadsetIcon size={15} className="shrink-0 text-subtle" />
                      <dt className="sr-only">Voice</dt>
                      <dd>{card.voice ?? "The default voice for its style"}</dd>
                    </div>
                    <div>
                      <dt className="sr-only">Recent calls</dt>
                      <dd className="text-muted">
                        <span className="font-semibold text-ink">{card.calls_recent}</span>{" "}
                        {card.calls_recent === 1 ? "call" : "calls"} in the last{" "}
                        {card.activity_days} days
                      </dd>
                    </div>
                  </dl>

                  {gone ? (
                    <p className="mt-auto text-sm text-subtle">
                      This receptionist was deleted. Its calls are kept on the account.
                    </p>
                  ) : (
                    <div className="mt-auto flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => open(card, "/dashboard/agent")}
                      >
                        Edit
                      </Button>
                      {card.phone_e164 ? (
                        // A real call to its real number: the only honest "test"
                        // available, since nothing here can place one for you.
                        <ButtonLink
                          size="sm"
                          variant="ghost"
                          href={`tel:${card.phone_e164}`}
                          leadingIcon={<PhoneIcon size={15} />}
                        >
                          Call it
                        </ButtonLink>
                      ) : (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => open(card, "/dashboard/setup")}
                        >
                          Finish setup
                        </Button>
                      )}
                    </div>
                  )}
                </Card>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
