"use client";

/**
 * Everyone who has called, most recent first.
 *
 * Contacts are read off the call history rather than kept in a separate CRM, so
 * the list can never disagree with the calls it summarizes. Names are what
 * callers *said*, and the page labels them that way. There is no email or tag
 * data behind a contact yet, and the page says so rather than drawing empty
 * columns for it.
 */

import { useMemo, useState, type ReactNode } from "react";

import { CallDetailDrawer } from "@/components/app/CallDetailDrawer";
import { useTenantData } from "@/components/app/TenantWorkspace";
import { useLoad } from "@/components/app/useLoad";
import { useNow } from "@/components/app/useNow";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { CopyButton } from "@/components/ui/CopyButton";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState, Skeleton } from "@/components/ui/Feedback";
import { TextField } from "@/components/ui/Field";
import { ChevronRightIcon, PhoneIcon, RefreshIcon, SearchIcon, UsersIcon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { listCalls, listContacts } from "@/lib/api";
import {
  formatDate,
  formatDateTime,
  formatDuration,
  formatPhone,
  formatRelative,
  humanize,
  initials,
  pluralize,
} from "@/lib/format";
import { callStatusMeta } from "@/lib/status";
import type { CallView, ContactView } from "@/lib/types";

function matches(contact: ContactView, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  if ((contact.name ?? "").toLowerCase().includes(needle)) return true;
  if ((contact.last_summary ?? "").toLowerCase().includes(needle)) return true;
  const digits = needle.replace(/\D/g, "");
  return digits.length >= 3 && contact.phone_e164.replace(/\D/g, "").includes(digits);
}

export function ContactsView() {
  const { tenantId } = useTenantData();
  const now = useNow();
  const contacts = useLoad(() => listContacts(tenantId), `contacts:${tenantId}`);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<ContactView | null>(null);
  const [call, setCall] = useState<CallView | null>(null);

  const data = contacts.data;
  const list = useMemo(() => data ?? [], [data]);
  const filtered = useMemo(() => list.filter((contact) => matches(contact, query)), [list, query]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Contacts"
        description="Everyone who has called your receptionist, built from your call history."
        actions={
          <Button
            variant="secondary"
            onClick={contacts.reload}
            loading={contacts.loading && contacts.data !== null}
            leadingIcon={<RefreshIcon size={16} />}
          >
            Refresh
          </Button>
        }
      />

      {contacts.error && (
        <Alert tone="danger" title="We couldn't load your contacts">
          {contacts.error}
        </Alert>
      )}

      {list.length > 0 && (
        <Card className="p-4 sm:p-5">
          <TextField
            label="Search contacts"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Name, number or what they called about"
          />
        </Card>
      )}

      <Card className="overflow-hidden">
        <CardHeader
          title={contacts.data ? pluralize(filtered.length, "contact") : "Contacts"}
          description="Most recent caller first"
        />
        {contacts.loading && !contacts.data ? (
          <div aria-busy="true" className="space-y-3 p-5">
            {[0, 1, 2].map((key) => (
              <Skeleton key={key} className="h-12 rounded-xl" />
            ))}
          </div>
        ) : list.length === 0 && !contacts.error ? (
          <div className="p-5">
            <EmptyState
              icon={<UsersIcon />}
              title="No contacts yet"
              description="Each new caller appears here after their first call, with their number, the name they gave and what they needed."
            />
          </div>
        ) : filtered.length === 0 && list.length > 0 ? (
          <div className="p-5">
            <EmptyState
              icon={<SearchIcon />}
              title="No contacts match"
              description="Try a name, part of a number, or something they called about."
            />
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {filtered.map((contact) => (
              <li key={contact.phone_e164}>
                <button
                  type="button"
                  onClick={() => setOpen(contact)}
                  aria-label={`View contact ${contact.name ?? formatPhone(contact.phone_e164)}`}
                  className="grid w-full grid-cols-[2.25rem_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-3.5 text-left transition-colors hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none md:grid-cols-[2.25rem_minmax(0,2fr)_9rem_6rem_9rem_1rem] md:gap-x-4 md:px-5"
                >
                  <span
                    aria-hidden
                    className="flex size-9 items-center justify-center rounded-full bg-surface-3 text-xs font-bold text-ink-2"
                  >
                    {initials(contact.name ?? "")}
                  </span>
                  <span className="min-w-0">
                    <span className="flex items-center gap-2">
                      <span className="truncate text-sm font-semibold text-ink">
                        {contact.name ?? formatPhone(contact.phone_e164)}
                      </span>
                      {contact.has_urgent && <Badge tone="warn">Urgent</Badge>}
                    </span>
                    <span className="mt-0.5 block truncate text-sm text-muted">
                      {contact.name ? `${formatPhone(contact.phone_e164)} · ` : ""}
                      {contact.last_summary ?? "No summary yet"}
                    </span>
                  </span>
                  <span className="text-right text-xs whitespace-nowrap text-muted md:text-left md:text-sm">
                    {contact.last_call_at ? formatRelative(contact.last_call_at, now) : "—"}
                  </span>
                  <span className="hidden text-sm tabular-nums text-ink-2 md:block">
                    {pluralize(contact.call_count, "call")}
                  </span>
                  <span className="hidden truncate text-sm text-ink-2 md:block">
                    {humanize(contact.last_intent)}
                  </span>
                  <ChevronRightIcon size={16} className="hidden text-subtle md:block" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <ContactDrawer
        contact={open}
        onClose={() => setOpen(null)}
        onOpenCall={(selected) => {
          setOpen(null);
          setCall(selected);
        }}
      />
      <CallDetailDrawer call={call} onClose={() => setCall(null)} />
    </div>
  );
}

function ContactDrawer({
  contact,
  onClose,
  onOpenCall,
}: {
  contact: ContactView | null;
  onClose: () => void;
  onOpenCall: (call: CallView) => void;
}) {
  return (
    <Dialog
      open={contact !== null}
      onClose={onClose}
      variant="drawer"
      title={contact ? (contact.name ?? formatPhone(contact.phone_e164)) : "Contact"}
      description={contact ? formatPhone(contact.phone_e164) : undefined}
    >
      {contact && <ContactDetail contact={contact} onOpenCall={onOpenCall} />}
    </Dialog>
  );
}

function ContactDetail({
  contact,
  onOpenCall,
}: {
  contact: ContactView;
  onOpenCall: (call: CallView) => void;
}) {
  const { tenantId } = useTenantData();
  const history = useLoad(
    () => listCalls(tenantId, undefined, { limit: 50, caller: contact.phone_e164 }),
    `contact-calls:${tenantId}:${contact.phone_e164}`,
  );

  return (
    <div className="space-y-6">
      <dl className="grid grid-cols-1 gap-4 rounded-xl border border-line bg-surface-2 p-4 text-sm sm:grid-cols-2">
        <Detail label="Name">
          {contact.name ?? "Not given"}
          {contact.name && <span className="mt-0.5 block text-xs text-muted">As stated by the caller</span>}
        </Detail>
        <Detail label="Phone">
          <span className="tabular-nums">{formatPhone(contact.phone_e164)}</span>
        </Detail>
        <Detail label="Email">Not captured on calls</Detail>
        <Detail label="Calls">{contact.call_count}</Detail>
        <Detail label="First call">{formatDate(contact.first_call_at)}</Detail>
        <Detail label="Last interaction">{formatDateTime(contact.last_call_at)}</Detail>
        <Detail label="Tags">Not available yet</Detail>
        <Detail label="Latest intent">{humanize(contact.last_intent)}</Detail>
      </dl>

      <div className="flex flex-wrap gap-2.5">
        <ButtonLink href={`tel:${contact.phone_e164}`} leadingIcon={<PhoneIcon size={16} />}>
          Call
        </ButtonLink>
        <CopyButton value={contact.phone_e164} label="Copy number" size="md" />
      </div>

      <section aria-labelledby="contact-history-heading">
        <h3
          id="contact-history-heading"
          className="text-xs font-semibold tracking-[0.06em] text-muted uppercase"
        >
          Call history
        </h3>
        <div className="mt-3">
          {history.loading && !history.data ? (
            <div aria-busy="true" className="space-y-2">
              <Skeleton className="h-14 rounded-xl" />
              <Skeleton className="h-14 rounded-xl" />
            </div>
          ) : history.error ? (
            <p className="text-sm text-muted">Their calls couldn&apos;t be loaded. {history.error}</p>
          ) : (
            <ul className="divide-y divide-line rounded-xl border border-line">
              {(history.data ?? []).map((call) => (
                <li key={call.id}>
                  <button
                    type="button"
                    onClick={() => onOpenCall(call)}
                    className="flex w-full items-start justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2"
                  >
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-ink">
                        {formatDateTime(call.started_at)}
                      </span>
                      <span className="mt-0.5 line-clamp-2 block text-sm text-muted">
                        {call.summary ?? "No summary yet"}
                      </span>
                    </span>
                    <span className="shrink-0 text-right text-xs text-muted">
                      {formatDuration(call.duration_s)}
                      <span className="mt-1 block">{callStatusMeta(call.status).label}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 font-medium break-words text-ink">{children}</dd>
    </div>
  );
}
