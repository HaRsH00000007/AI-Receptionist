"use client";

/**
 * Agents → Tags: the labels a business sorts its calls by.
 *
 * A tag is two things at once, and the panel has to make that obvious: a
 * coloured label a person reads on a call, and a *sentence the model reads* to
 * decide whether the label applies. The description is not documentation — it
 * is the rule — which is why it sits on the row rather than behind the edit
 * dialog, and why the dialog's hint says what it is for.
 *
 * Three things this page is careful about:
 *
 * **Off is not deleted, and deleted is not off.** Switching a tag off keeps it
 * on the calls it already marked and stops it being applied again. Deleting it
 * removes it from those calls too. The confirmation says so in those words,
 * because the difference is the kind of thing somebody discovers afterwards.
 *
 * **The limit counts active tags.** Twenty-five descriptions is already a lot to
 * ask one model call to weigh, and a disabled tag is not in that prompt — so
 * retiring one frees a slot without destroying the history it explains.
 *
 * **Tags are not the intent.** The Calls page already shows an `intent`, which
 * is one free-text guess the summary made. These are the business's own closed
 * set, and both are shown.
 */

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useCanManage, useTenantData } from "@/components/app/TenantWorkspace";
import { useLoad } from "@/components/app/useLoad";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { ConfirmDialog, Dialog } from "@/components/ui/Dialog";
import { EmptyState, Skeleton } from "@/components/ui/Feedback";
import { SelectField, TextAreaField, TextField } from "@/components/ui/Field";
import { ListIcon, PlusIcon, TagIcon } from "@/components/ui/icons";
import { Menu } from "@/components/ui/Menu";
import { useToast } from "@/components/ui/Toast";
import { Toggle } from "@/components/ui/Toggle";
import { ApiError, createCallTag, deleteCallTag, listCallTags, updateCallTag } from "@/lib/api";
import { TAG_COLORS, tagColorToken, type CallTagView, type TagColor } from "@/lib/types";

const NAME_MAX_LENGTH = 40;
const DESCRIPTION_MAX_LENGTH = 300;

/** The coloured dot beside a tag's name, and on a call. */
export function TagDot({ color, size = 10 }: { color: TagColor; size?: number }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block shrink-0 rounded-full"
      style={{
        width: size,
        height: size,
        background: `var(${tagColorToken(color)})`,
      }}
    />
  );
}

interface TagValues {
  name: string;
  description: string;
  color: TagColor;
}

export function AgentTags() {
  const { tenantId } = useTenantData();
  const canManage = useCanManage();
  const router = useRouter();
  const toast = useToast();

  const listing = useLoad(() => listCallTags(tenantId), `call-tags:${tenantId}`);
  const { data, error, loading } = listing;

  const [editing, setEditing] = useState<CallTagView | null>(null);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<CallTagView | null>(null);
  const [busy, setBusy] = useState(false);

  const tags = data?.tags ?? [];
  const active = data?.active ?? 0;
  const maxTags = data?.max_tags ?? 25;
  const atLimit = active >= maxTags;

  function fail(caught: unknown, fallback: string) {
    const failure = caught instanceof ApiError ? caught : null;
    toast({ tone: "danger", title: fallback, description: failure?.message });
  }

  async function save(values: TagValues): Promise<boolean> {
    setBusy(true);
    try {
      const result = editing
        ? await updateCallTag(tenantId, editing.id, values)
        : await createCallTag(tenantId, values);
      listing.set(result);
      toast({ tone: "success", title: editing ? "Tag updated" : "Tag added" });
      setEditing(null);
      setAdding(false);
      return true;
    } catch (caught) {
      fail(caught, editing ? "Couldn't update that tag" : "Couldn't add that tag");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function toggle(tag: CallTagView) {
    try {
      listing.set(await updateCallTag(tenantId, tag.id, { enabled: !tag.enabled }));
    } catch (caught) {
      fail(caught, "Couldn't change that tag");
    }
  }

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try {
      listing.set(await deleteCallTag(tenantId, removing.id));
      toast({ tone: "success", title: "Tag removed" });
      setRemoving(null);
    } catch (caught) {
      fail(caught, "Couldn't remove that tag");
    } finally {
      setBusy(false);
    }
  }

  if (loading && !data) {
    return (
      <Card>
        <div className="space-y-2 p-5">
          <Skeleton className="h-10 rounded-lg" />
          <Skeleton className="h-10 rounded-lg" />
        </div>
      </Card>
    );
  }

  if (error) {
    return (
      <Alert
        tone="danger"
        title="Your tags couldn't be loaded"
        action={
          <Button size="sm" variant="secondary" onClick={() => listing.reload()}>
            Try again
          </Button>
        }
      >
        {error}
      </Alert>
    );
  }

  const addButton = canManage ? (
    <Button
      size="sm"
      leadingIcon={<PlusIcon size={15} />}
      disabled={atLimit}
      onClick={() => setAdding(true)}
    >
      Add tag
    </Button>
  ) : undefined;

  return (
    <div className="space-y-6">
      {tags.length === 0 ? (
        <Card>
          <EmptyState
            className="border-0"
            icon={<TagIcon size={22} />}
            title="Sort your calls by what they were about"
            description={
              canManage
                ? "Add a tag and describe when it applies. Your receptionist labels every call it answers from then on."
                : "Only an owner or admin can set up tags."
            }
            action={addButton}
          />
        </Card>
      ) : (
        <Card>
          <CardHeader
            title={
              <span className="flex items-center gap-2">
                <TagIcon size={18} className="text-muted" />
                Tags
              </span>
            }
            description={`${active} of ${maxTags} active. Switched-off tags stay on the calls they already marked.`}
            action={addButton}
          />
          <ul className="divide-y divide-line border-t border-line">
            {tags.map((tag) => (
              <li key={tag.id} className="flex items-start gap-3 p-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <TagDot color={tag.color} />
                    <span className="truncate font-semibold text-ink">{tag.name}</span>
                  </div>
                  <p className="mt-1 text-sm text-muted">{tag.description}</p>
                </div>
                <Toggle
                  label={tag.enabled ? `Switch off ${tag.name}` : `Switch on ${tag.name}`}
                  on={tag.enabled}
                  disabled={!canManage || (!tag.enabled && atLimit)}
                  onToggle={() => void toggle(tag)}
                />
                {canManage ? (
                  <Menu
                    label={`Actions for ${tag.name}`}
                    iconOnly
                    trigger="⋯"
                    width="w-56"
                    items={[
                      { label: "Edit tag", onSelect: () => setEditing(tag) },
                      {
                        label: "See tagged calls",
                        // The calls page reads `?tag=` and asks the server for
                        // that tag's calls, rather than narrowing the page it
                        // already has.
                        onSelect: () => router.push(`/dashboard/calls?tag=${tag.id}`),
                      },
                      {
                        label: "Delete tag",
                        description: "Also removes it from past calls.",
                        tone: "danger",
                        onSelect: () => setRemoving(tag),
                      },
                    ]}
                  />
                ) : null}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {atLimit ? (
        <Alert tone="info">
          That&apos;s the most active tags a receptionist can have. Switch one off to free a slot —
          it keeps the calls it already marked.
        </Alert>
      ) : null}

      {adding || editing ? (
        <TagDialog
          tag={editing}
          busy={busy}
          onClose={() => {
            setAdding(false);
            setEditing(null);
          }}
          onSave={save}
        />
      ) : null}

      <ConfirmDialog
        open={removing !== null}
        title={`Delete "${removing?.name ?? ""}"?`}
        confirmLabel="Delete tag"
        busy={busy}
        onCancel={() => setRemoving(null)}
        onConfirm={() => void remove()}
      >
        This also removes the tag from every call it was applied to, which cannot be undone. To stop
        using it without losing that history, switch it off instead.
      </ConfirmDialog>
    </div>
  );
}

// ---------------------------------------------------------------------------

function TagDialog({
  tag,
  busy,
  onClose,
  onSave,
}: {
  tag: CallTagView | null;
  busy: boolean;
  onClose: () => void;
  onSave: (values: TagValues) => Promise<boolean>;
}) {
  const [name, setName] = useState(tag?.name ?? "");
  const [description, setDescription] = useState(tag?.description ?? "");
  const [color, setColor] = useState<TagColor>(tag?.color ?? "slate");
  const [problem, setProblem] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    // Checked here only to save a round trip; the API is the authority on both.
    if (!name.trim()) {
      setProblem("Give the tag a name.");
      return;
    }
    if (!description.trim()) {
      setProblem("Say when your receptionist should apply this tag.");
      return;
    }
    setProblem(null);
    await onSave({ name: name.trim(), description: description.trim(), color });
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={tag ? "Edit tag" : "Add tag"}
      description="Your receptionist reads the description to decide which calls this tag belongs on."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button form="call-tag-form" type="submit" loading={busy}>
            Save tag
          </Button>
        </>
      }
    >
      <form id="call-tag-form" className="space-y-4" noValidate onSubmit={submit}>
        {problem && <Alert tone="danger">{problem}</Alert>}
        <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_10rem]">
          <TextField
            label="Tag name"
            value={name}
            maxLength={NAME_MAX_LENGTH}
            onChange={(event) => setName(event.target.value)}
            placeholder="Pricing"
            data-autofocus
          />
          <SelectField
            label="Colour"
            value={color}
            options={TAG_COLORS.map((option) => ({
              value: option.value,
              label: option.label,
            }))}
            onChange={(event) => setColor(event.target.value as TagColor)}
          />
        </div>
        <TextAreaField
          label="Description"
          rows={4}
          maxLength={DESCRIPTION_MAX_LENGTH}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="Caller asks about pricing, charges, invoices, refunds, or any billing-related question."
          hint="Tell your receptionist what this tag means so it can apply it correctly. Describe the call, not the steps."
        />
        <p className="flex items-center gap-2 text-sm text-muted">
          <ListIcon size={15} className="shrink-0 text-subtle" />
          Applied after each call, from the transcript. Existing calls are not relabelled.
        </p>
      </form>
    </Dialog>
  );
}
