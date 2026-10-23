"use client";

/**
 * Agents → Transfer calls: when the receptionist should fetch a person.
 *
 * One rule is one sentence a business already knows how to say — *if a caller
 * asks about billing, ring the office* — so the form asks for exactly that: a
 * number, and when to use it. The condition is what the voice agent reads at
 * call time to decide whether this call is one of them.
 *
 * Three things this page is careful about, each because the alternative is
 * worse than a missing feature:
 *
 * **A saved rule is not necessarily a live one.** Saving republishes the
 * configuration and pushes it to the voice agent. When that push fails the API
 * says so, and this page repeats what it said rather than showing a green tick
 * — for a *deleted* rule the difference matters, because callers may still be
 * being transferred.
 *
 * **Off is not deleted.** A business closing for a week should be able to stop
 * forwarding without retyping a number, so a rule can be switched off and stays
 * on file.
 *
 * **Cold transfers are only offered where they work.** They need the number to
 * have been imported into the voice vendor from Twilio natively; the server
 * says whether that holds, and the choice is hidden rather than offered and
 * refused.
 */

import { useState, type FormEvent } from "react";

import { useCanManage, useTenantData } from "@/components/app/TenantWorkspace";
import { useLoad } from "@/components/app/useLoad";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { ConfirmDialog, Dialog } from "@/components/ui/Dialog";
import { EmptyState, Skeleton } from "@/components/ui/Feedback";
import { ChoiceGroup, TextAreaField, TextField } from "@/components/ui/Field";
import { PhoneForwardIcon, PhoneIcon, PlusIcon } from "@/components/ui/icons";
import { Menu } from "@/components/ui/Menu";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  createTransferRule,
  deleteTransferRule,
  listTransferRules,
  updateTransferRule,
} from "@/lib/api";
import { formatPhone } from "@/lib/format";
import type { TransferRuleResult, TransferRuleView, TransferType } from "@/lib/types";

const CONDITION_MAX_LENGTH = 300;

/** Values the dialog collects. The API normalizes the number. */
interface RuleValues {
  destination: string;
  condition: string;
  label: string;
  transferType: TransferType;
}

export function AgentTransfers() {
  const { tenantId } = useTenantData();
  const canManage = useCanManage();
  const toast = useToast();

  const listing = useLoad(() => listTransferRules(tenantId), `transfer-rules:${tenantId}`);
  const { data, error, loading } = listing;

  const [editing, setEditing] = useState<TransferRuleView | null>(null);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<TransferRuleView | null>(null);
  const [busy, setBusy] = useState(false);
  // What the last save reported. Kept on the page, not only in a toast: when a
  // push to the voice agent fails, the fact that calls are still being handled
  // the old way has to stay on screen.
  const [notice, setNotice] = useState<{
    tone: "success" | "warn";
    text: string;
  } | null>(null);

  const rules = data?.rules ?? [];
  const coldAvailable = data?.cold_transfer_available ?? false;
  const atLimit = data ? rules.length >= data.max_rules : false;

  function fail(caught: unknown, fallback: string) {
    const failure = caught instanceof ApiError ? caught : null;
    toast({ tone: "danger", title: fallback, description: failure?.message });
  }

  /** Fold a write's answer back into the page: the list, and what it did. */
  function applyResult(result: TransferRuleResult, title: string) {
    listing.set(result);
    setNotice({
      tone: result.synced ? "success" : "warn",
      text: result.detail,
    });
    toast({
      tone: result.synced ? "success" : "info",
      title,
      description: result.detail,
    });
  }

  async function save(values: RuleValues): Promise<boolean> {
    setBusy(true);
    try {
      const payload = {
        destination_number: values.destination,
        condition: values.condition,
        label: values.label,
        transfer_type: values.transferType,
      };
      const result = editing
        ? await updateTransferRule(tenantId, editing.id, payload)
        : await createTransferRule(tenantId, payload);
      applyResult(result, editing ? "Rule updated" : "Rule added");
      setEditing(null);
      setAdding(false);
      return true;
    } catch (caught) {
      fail(caught, editing ? "Couldn't update that rule" : "Couldn't add that rule");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function toggle(rule: TransferRuleView) {
    try {
      const result = await updateTransferRule(tenantId, rule.id, {
        enabled: !rule.enabled,
      });
      applyResult(result, rule.enabled ? "Rule switched off" : "Rule switched on");
    } catch (caught) {
      fail(caught, "Couldn't change that rule");
    }
  }

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try {
      const result = await deleteTransferRule(tenantId, removing.id);
      applyResult(result, "Rule removed");
      setRemoving(null);
    } catch (caught) {
      fail(caught, "Couldn't remove that rule");
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
        title="Transfer rules couldn't be loaded"
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
      leadingIcon={<PlusIcon size={16} />}
      disabled={atLimit}
      onClick={() => {
        setNotice(null);
        setAdding(true);
      }}
    >
      Add transferring rule
    </Button>
  ) : undefined;

  return (
    <div className="space-y-6">
      {notice ? <Alert tone={notice.tone}>{notice.text}</Alert> : null}

      {rules.length === 0 ? (
        <Card>
          <EmptyState
            className="border-0"
            icon={<PhoneForwardIcon size={22} />}
            title="Want certain calls to go to a real person?"
            description={
              canManage
                ? "Set up rules to transfer calls to yourself or your team."
                : "Only an owner or admin can set up transfer rules."
            }
            action={addButton}
          />
        </Card>
      ) : (
        <Card>
          <CardHeader
            title={
              <span className="flex items-center gap-2">
                <PhoneForwardIcon size={18} className="text-muted" />
                Transfer rules
              </span>
            }
            description="Checked in order. The first one that matches the call is the one used."
            action={addButton}
          />
          <ul className="divide-y divide-line">
            {rules.map((rule) => (
              <li key={rule.id} className="flex items-start gap-3 p-4">
                <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-muted">
                  <PhoneIcon size={16} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-ink">
                      {rule.label || formatPhone(rule.destination_e164)}
                    </span>
                    <Badge tone={rule.transfer_type === "warm" ? "accent" : "neutral"}>
                      {rule.transfer_type === "warm" ? "Announced" : "Straight through"}
                    </Badge>
                    {!rule.enabled && <Badge tone="warn">Off</Badge>}
                  </div>
                  {rule.label ? (
                    <p className="text-sm text-muted">{formatPhone(rule.destination_e164)}</p>
                  ) : null}
                  <p className="mt-1 text-sm text-muted">Transfers when {rule.condition}</p>
                </div>
                {canManage ? (
                  <Menu
                    label={`Actions for ${rule.label || formatPhone(rule.destination_e164)}`}
                    iconOnly
                    trigger="⋯"
                    width="w-56"
                    items={[
                      {
                        label: "Edit rule",
                        onSelect: () => {
                          setNotice(null);
                          setEditing(rule);
                        },
                      },
                      {
                        label: rule.enabled ? "Switch off" : "Switch on",
                        description: rule.enabled
                          ? "Keeps the rule, stops the transfers."
                          : undefined,
                        onSelect: () => void toggle(rule),
                      },
                      {
                        label: "Delete rule",
                        tone: "danger",
                        onSelect: () => setRemoving(rule),
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
          That&apos;s the most rules a receptionist can have. Delete one to add another — a
          receptionist choosing between more destinations than this chooses badly.
        </Alert>
      ) : null}

      {adding || editing ? (
        <RuleDialog
          rule={editing}
          busy={busy}
          coldAvailable={coldAvailable}
          onClose={() => {
            setAdding(false);
            setEditing(null);
          }}
          onSave={save}
        />
      ) : null}

      <ConfirmDialog
        open={removing !== null}
        title="Delete this transfer rule?"
        confirmLabel="Delete rule"
        busy={busy}
        onCancel={() => setRemoving(null)}
        onConfirm={() => void remove()}
      >
        Calls that match it will be handled by your receptionist again — it will take a message
        instead of transferring. You can add the rule back at any time.
      </ConfirmDialog>
    </div>
  );
}

// ---------------------------------------------------------------------------

function RuleDialog({
  rule,
  busy,
  coldAvailable,
  onClose,
  onSave,
}: {
  rule: TransferRuleView | null;
  busy: boolean;
  coldAvailable: boolean;
  onClose: () => void;
  onSave: (values: RuleValues) => Promise<boolean>;
}) {
  const [destination, setDestination] = useState(rule?.destination_e164 ?? "");
  const [condition, setCondition] = useState(rule?.condition ?? "");
  const [label, setLabel] = useState(rule?.label ?? "");
  const [transferType, setTransferType] = useState<TransferType>(rule?.transfer_type ?? "warm");
  const [problem, setProblem] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    // Checked here only to save a round trip; the API is the authority on both.
    if (destination.replace(/\D/g, "").length < 10) {
      setProblem("Enter a 10-digit US phone number.");
      return;
    }
    if (!condition.trim()) {
      setProblem("Say when this rule should transfer a call.");
      return;
    }
    setProblem(null);
    await onSave({
      destination,
      condition: condition.trim(),
      label: label.trim(),
      transferType,
    });
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={rule ? "Edit transferring rule" : "Add transferring rule"}
      description="Your receptionist decides from the description below whether a call should be transferred."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button form="transfer-rule-form" type="submit" loading={busy}>
            {rule ? "Save changes" : "Add rule"}
          </Button>
        </>
      }
    >
      <form id="transfer-rule-form" className="space-y-4" noValidate onSubmit={submit}>
        {problem && <Alert tone="danger">{problem}</Alert>}
        <TextField
          label="Transfer to"
          value={destination}
          onChange={(event) => setDestination(event.target.value)}
          placeholder="(205) 555-0143"
          hint="A US number. Your caller is connected to it during the call."
          data-autofocus
        />
        <TextAreaField
          label="When should calls be transferred?"
          rows={3}
          maxLength={CONDITION_MAX_LENGTH}
          value={condition}
          onChange={(event) => setCondition(event.target.value)}
          placeholder="the caller asks to speak to a person about billing"
          hint="Describe the calls, not the steps. Your receptionist reads this."
        />
        <TextField
          label="Name"
          optional
          maxLength={80}
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          hint="For your own list. Callers never hear it."
        />
        {coldAvailable ? (
          <ChoiceGroup
            legend="How to hand the call over"
            name="transfer-type"
            value={transferType}
            onChange={setTransferType}
            options={[
              {
                value: "warm",
                label: "Announced",
                description:
                  "We call them, say who's on the line and why, then connect the caller.",
              },
              {
                value: "cold",
                label: "Straight through",
                description: "Connected immediately, showing the caller's own number.",
              },
            ]}
          />
        ) : (
          <Alert tone="info">
            Calls are transferred with an announcement: we tell whoever answers who is on the line
            before connecting them.
          </Alert>
        )}
      </form>
    </Dialog>
  );
}
