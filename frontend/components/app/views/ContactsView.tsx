"use client";

/**
 * Contacts: the people a business keeps on file.
 *
 * Contacts used to be a read-only view over the call history. They are records
 * now — editable, blockable, importable — and this page is the whole surface
 * for them: search, two tabs, a table, and four actions.
 *
 * Two things the copy here is careful about:
 *
 * **A caller with no name is "Unknown caller", never a guess.** The number is
 * the useful part, and inventing a name would put it in front of a receptionist
 * to say out loud.
 *
 * **Blocking says what it does.** It moves someone to the Blocked tab and
 * records the decision. It does not hang up on them, and the dialog says so
 * rather than letting a business assume otherwise.
 */

import { useEffect, useMemo, useState, type FormEvent } from "react";

import { useCanManage, useTenantData } from "@/components/app/TenantWorkspace";
import { useLoad } from "@/components/app/useLoad";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog, Dialog } from "@/components/ui/Dialog";
import { EmptyState, Skeleton } from "@/components/ui/Feedback";
import { TextAreaField, TextField } from "@/components/ui/Field";
import {
  DownloadIcon,
  PlusIcon,
  UploadIcon,
  UsersIcon,
} from "@/components/ui/icons";
import { Menu } from "@/components/ui/Menu";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  contactsExportUrl,
  createContact,
  deleteContact,
  importContacts,
  listContacts,
  setContactBlocked,
  updateContact,
} from "@/lib/api";
import { formatDate, formatPhone } from "@/lib/format";
import type { ContactImportResult, ContactView } from "@/lib/types";

const PAGE_SIZE = 25;
type Tab = "active" | "blocked";

export function ContactsView() {
  const { tenantId } = useTenantData();
  const canManage = useCanManage();
  const toast = useToast();

  const [tab, setTab] = useState<Tab>("active");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);

  const [editing, setEditing] = useState<ContactView | null>(null);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<ContactView | null>(null);
  const [importing, setImporting] = useState(false);
  const [busy, setBusy] = useState(false);

  // The shared loader: keyed on everything that changes the result, so a tab,
  // a search or a page turn re-runs it, and a response that arrives after the
  // next request is dropped rather than rendered over fresher data.
  const listing = useLoad(
    () =>
      listContacts(tenantId, {
        search: query,
        status: tab,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    `contacts:${tenantId}:${tab}:${query}:${page}`,
  );
  const { data, error, loading } = listing;
  const load = listing.reload;

  // Debounced: typing a name should not fire a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search.trim());
      setPage(0);
    }, 350);
    return () => clearTimeout(timer);
  }, [search]);

  const contacts = data?.contacts ?? [];
  const pages = useMemo(
    () => (data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1),
    [data],
  );

  function fail(caught: unknown, fallback: string) {
    const failure = caught instanceof ApiError ? caught : null;
    toast({ tone: "danger", title: fallback, description: failure?.message });
  }

  async function save(values: { phone: string; name: string; note: string }) {
    setBusy(true);
    try {
      if (editing) {
        await updateContact(tenantId, editing.id, {
          phone_number: values.phone,
          full_name: values.name,
          note: values.note,
        });
        toast({ tone: "success", title: "Contact updated" });
      } else {
        await createContact(tenantId, {
          phone_number: values.phone,
          full_name: values.name,
          note: values.note,
        });
        toast({ tone: "success", title: "Contact added" });
      }
      setEditing(null);
      setAdding(false);
      load();
      return true;
    } catch (caught) {
      fail(caught, editing ? "Couldn't update that contact" : "Couldn't add that contact");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try {
      await deleteContact(tenantId, removing.id);
      toast({
        tone: "success",
        title: "Contact removed",
        description: "Their call history is unchanged.",
      });
      setRemoving(null);
      load();
    } catch (caught) {
      fail(caught, "Couldn't remove that contact");
    } finally {
      setBusy(false);
    }
  }

  async function block(contact: ContactView, blocked: boolean) {
    try {
      await setContactBlocked(tenantId, contact.id, blocked);
      toast({
        tone: "success",
        title: blocked ? "Contact blocked" : "Contact unblocked",
        description: blocked
          ? "They've moved to the Blocked tab. Their calls are still answered."
          : undefined,
      });
      load();
    } catch (caught) {
      fail(caught, blocked ? "Couldn't block that contact" : "Couldn't unblock that contact");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Contacts"
        description="Everyone who has called, plus anyone you add yourself."
        actions={
          canManage ? (
            <div className="flex flex-wrap gap-2.5">
              <Button leadingIcon={<PlusIcon size={16} />} onClick={() => setAdding(true)}>
                Add contact
              </Button>
              <Button
                variant="secondary"
                leadingIcon={<UploadIcon size={16} />}
                onClick={() => setImporting(true)}
              >
                Import CSV
              </Button>
              <a
                className="btn btn-secondary btn-md"
                href={contactsExportUrl(tenantId, query, tab)}
                download
              >
                <DownloadIcon size={16} />
                Export CSV
              </a>
            </div>
          ) : undefined
        }
      />

      <Card>
        <div className="flex flex-col gap-3 border-b border-line p-4 sm:flex-row sm:items-center sm:justify-between">
          <TextField
            label="Search contacts"
            hint=""
            className="sm:w-80"
            placeholder="Search by name or phone number"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <div
            role="tablist"
            aria-label="Contact status"
            className="inline-flex shrink-0 gap-1 rounded-xl bg-surface-2 p-1"
          >
            {(["active", "blocked"] as const).map((value) => (
              <button
                key={value}
                role="tab"
                type="button"
                aria-selected={tab === value}
                onClick={() => {
                  setTab(value);
                  setPage(0);
                }}
                className={
                  tab === value
                    ? "rounded-lg bg-surface px-4 py-1.5 text-sm font-semibold text-ink shadow-xs"
                    : "rounded-lg px-4 py-1.5 text-sm font-medium text-muted hover:text-ink"
                }
              >
                {value === "active" ? "Active" : "Blocked"} (
                {value === "active" ? (data?.active_count ?? 0) : (data?.blocked_count ?? 0)})
              </button>
            ))}
          </div>
        </div>

        {error ? (
          <div className="p-5">
            <Alert
              tone="danger"
              title="Contacts couldn't be loaded"
              action={
                <Button size="sm" variant="secondary" onClick={() => load()}>
                  Try again
                </Button>
              }
            >
              {error}
            </Alert>
          </div>
        ) : loading && !data ? (
          <div className="space-y-2 p-5">
            <Skeleton className="h-10 rounded-lg" />
            <Skeleton className="h-10 rounded-lg" />
            <Skeleton className="h-10 rounded-lg" />
          </div>
        ) : contacts.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={<UsersIcon size={22} />}
              title={query ? "No contacts match that search" : "No contacts yet"}
              description={
                query
                  ? "Try a different name or number."
                  : "Contacts captured from completed calls will appear here."
              }
              action={
                canManage && !query ? (
                  <Button size="sm" onClick={() => setAdding(true)}>
                    Add your first contact
                  </Button>
                ) : undefined
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Full name</th>
                  <th scope="col">Phone number</th>
                  <th scope="col">Note</th>
                  <th scope="col">Calls</th>
                  <th scope="col">Date added</th>
                  {canManage && (
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {contacts.map((contact) => (
                  <tr key={contact.id}>
                    <td className="font-semibold text-ink">
                      {contact.full_name ?? <span className="text-muted">Unknown caller</span>}
                      {contact.status === "blocked" && (
                        <Badge tone="warn" className="ml-2">
                          Blocked
                        </Badge>
                      )}
                    </td>
                    <td className="tabular-nums">{formatPhone(contact.phone_e164)}</td>
                    <td className="max-w-xs truncate text-muted">{contact.note ?? "—"}</td>
                    <td className="tabular-nums">{contact.call_count}</td>
                    <td className="text-muted">{formatDate(contact.created_at)}</td>
                    {canManage && (
                      <td className="text-right">
                        <Menu
                          label={`Actions for ${contact.full_name ?? contact.phone_e164}`}
                          trigger="⋯"
                          iconOnly
                          align="end"
                          items={[
                            { label: "Edit", onSelect: () => setEditing(contact) },
                            {
                              label: contact.status === "blocked" ? "Unblock" : "Block",
                              onSelect: () => void block(contact, contact.status !== "blocked"),
                            },
                            {
                              label: "Remove",
                              tone: "danger",
                              onSelect: () => setRemoving(contact),
                            },
                          ]}
                        />
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pages > 1 && (
          <div className="flex items-center justify-between gap-3 border-t border-line px-5 py-3 text-sm">
            <span className="text-muted">
              Page <span className="tabular-nums">{page + 1}</span> of{" "}
              <span className="tabular-nums">{pages}</span>
            </span>
            <span className="flex gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={page === 0}
                onClick={() => setPage((current) => Math.max(0, current - 1))}
              >
                Previous
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={page + 1 >= pages}
                onClick={() => setPage((current) => current + 1)}
              >
                Next
              </Button>
            </span>
          </div>
        )}
      </Card>

      {(adding || editing) && (
        <ContactDialog
          contact={editing}
          busy={busy}
          onClose={() => {
            setAdding(false);
            setEditing(null);
          }}
          onSave={save}
        />
      )}

      <ConfirmDialog
        open={removing !== null}
        title="Remove this contact?"
        confirmLabel={busy ? "Removing…" : "Remove contact"}
        busy={busy}
        onConfirm={() => void remove()}
        onCancel={() => setRemoving(null)}
      >
        {removing && (
          <>
            <p>
              <span className="font-medium text-ink">
                {removing.full_name ?? "This caller"}
              </span>{" "}
              ({formatPhone(removing.phone_e164)}) will be removed from your contacts.
            </p>
            <p className="mt-2">
              Their calls, transcripts and summaries are kept — this only clears the address-book
              entry.
            </p>
          </>
        )}
      </ConfirmDialog>

      {importing && (
        <ImportDialog
          tenantId={tenantId}
          onClose={() => setImporting(false)}
          onImported={() => load()}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add / edit
// ---------------------------------------------------------------------------

function ContactDialog({
  contact,
  busy,
  onClose,
  onSave,
}: {
  contact: ContactView | null;
  busy: boolean;
  onClose: () => void;
  onSave: (values: { phone: string; name: string; note: string }) => Promise<boolean>;
}) {
  const [phone, setPhone] = useState(contact?.phone_e164 ?? "");
  const [name, setName] = useState(contact?.full_name ?? "");
  const [note, setNote] = useState(contact?.note ?? "");
  const [problem, setProblem] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const digits = phone.replace(/\D/g, "");
    if (digits.length < 10) {
      setProblem("Enter a 10-digit US phone number.");
      return;
    }
    setProblem(null);
    await onSave({ phone, name: name.trim(), note: note.trim() });
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={contact ? "Edit contact" : "Add contact"}
      description={
        contact ? undefined : "Anyone you add here is available to your receptionist immediately."
      }
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button form="contact-form" type="submit" loading={busy}>
            {contact ? "Save changes" : "Add contact"}
          </Button>
        </>
      }
    >
      <form id="contact-form" className="space-y-4" noValidate onSubmit={submit}>
        {problem && <Alert tone="danger">{problem}</Alert>}
        <TextField
          label="Phone number"
          value={phone}
          onChange={(event) => setPhone(event.target.value)}
          placeholder="(805) 555-0142"
          data-autofocus
        />
        <TextField
          label="Full name"
          optional
          value={name}
          onChange={(event) => setName(event.target.value)}
          hint="Leave blank if you don't know it — we won't guess."
        />
        <TextAreaField
          label="Note"
          optional
          rows={3}
          maxLength={2000}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          hint="Only you see this. Your receptionist never reads it out."
        />
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------

function ImportDialog({
  tenantId,
  onClose,
  onImported,
}: {
  tenantId: string;
  onClose: () => void;
  onImported: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ContactImportResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function upload() {
    if (!file) return;
    setBusy(true);
    setProblem(null);
    try {
      const report = await importContacts(tenantId, file);
      setResult(report);
      onImported();
    } catch (caught) {
      setProblem(
        caught instanceof ApiError ? caught.message : "That file couldn't be imported.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title="Import contacts"
      description="A CSV with a phone number column. A name and a note are optional."
      footer={
        result ? (
          <Button onClick={onClose}>Done</Button>
        ) : (
          <>
            <Button variant="secondary" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button loading={busy} disabled={!file} onClick={() => void upload()}>
              Import
            </Button>
          </>
        )
      }
    >
      {result ? (
        <div className="space-y-3">
          <Alert tone={result.invalid > 0 ? "warn" : "success"} title="Import finished">
            <span className="tabular-nums">{result.imported}</span> added,{" "}
            <span className="tabular-nums">{result.updated}</span> updated,{" "}
            <span className="tabular-nums">{result.skipped_duplicate}</span> already on file,{" "}
            <span className="tabular-nums">{result.invalid}</span> couldn&apos;t be read.
          </Alert>
          {result.errors.length > 0 && (
            <div className="rounded-xl border border-line">
              <p className="border-b border-line px-4 py-2 text-xs font-semibold text-muted">
                Rows we couldn&apos;t import
              </p>
              <ul className="divide-y divide-line text-sm">
                {result.errors.map((entry) => (
                  <li key={entry.row} className="flex gap-3 px-4 py-2">
                    <span className="shrink-0 text-muted tabular-nums">Row {entry.row}</span>
                    <span className="min-w-0 text-ink-2">{entry.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          {problem && <Alert tone="danger">{problem}</Alert>}
          <label className="block">
            <span className="field-label">CSV file</span>
            <input
              type="file"
              accept=".csv,text/csv"
              className="field mt-1.5 !py-2"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <p className="text-xs text-muted">
            Numbers already on file are left as they are — an import fills in blanks, it never
            overwrites what you have typed. Every row we can&apos;t read is listed back to you.
          </p>
        </div>
      )}
    </Dialog>
  );
}
