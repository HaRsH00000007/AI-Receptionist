"use client";

/**
 * Ending the service: delete the receptionist, and decide what happens to the
 * phone number.
 *
 * Two different consequences, so the dialog states them separately rather than
 * behind one word:
 *
 * * **Deleting** stops the receptionist answering. That part is reversible in
 *   the sense that a new receptionist can be set up afterwards — but the old
 *   one is gone, and callers reaching the number hear that it is out of service.
 * * **Releasing the number** hands it back to the carrier. That is the
 *   irreversible half: the number may go to somebody else and usually cannot be
 *   bought back. It is off by default and has to be ticked, because the
 *   recoverable choice should be the one you get by not reading carefully.
 *
 * Keeping the number is not free, and the dialog says so. A customer who keeps
 * it to reuse later would otherwise be surprised by the next invoice.
 *
 * Owners only. Admins can edit the receptionist but not end the service, and
 * the API enforces that independently of this component.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

import { useTenantData } from "@/components/app/TenantWorkspace";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { ApiError, deleteReceptionist } from "@/lib/api";
import { formatPhone } from "@/lib/format";
import { NEW_BUSINESS_PATH } from "@/lib/routing";

export function DeleteReceptionist() {
  const { tenant, phone, tenantId, refresh } = useTenantData();
  const toast = useToast();
  const router = useRouter();

  const [open, setOpen] = useState(false);
  const [release, setRelease] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [leftover, setLeftover] = useState<string | null>(null);

  // A number we did not buy cannot be handed back by us, so the option is not
  // offered rather than offered and then refused.
  const releasable = Boolean(phone?.can_release);

  function close() {
    if (deleting) return;
    setOpen(false);
    setRelease(false);
  }

  async function confirm() {
    if (deleting) return; // a second click must not send a second delete
    setDeleting(true);
    setLeftover(null);
    try {
      const outcome = await deleteReceptionist(tenantId, release && releasable);
      setOpen(false);
      setRelease(false);
      if (outcome.complete) {
        toast({
          tone: "success",
          title: "Your receptionist was deleted",
          description: outcome.number_released
            ? "The phone number has been released."
            : outcome.number_retained
              ? `${formatPhone(outcome.number_retained)} is still on your account.`
              : "It has stopped answering calls.",
        });
        // Straight to the setup form. The dashboard would otherwise route this
        // cancelled business to the setup page, which can only say that the
        // receptionist is gone — a dead end right after a deliberate delete.
        router.push(NEW_BUSINESS_PATH);
        return;
      } else {
        // Never reported as a clean success: something at a vendor is unfinished.
        setLeftover(
          outcome.number_retained
            ? `Your receptionist has stopped answering, but ${formatPhone(outcome.number_retained)} could not be released. Try again, or contact support if it keeps failing.`
            : "Your receptionist has stopped answering, but some cleanup did not finish. Try again, or contact support if it keeps failing.",
        );
        toast({
          tone: "info",
          title: "Stopped, but cleanup is unfinished",
          description: "Some of this could not be completed. You can safely try again.",
        });
        // Deliberately stays put: the retry button is on this page, and leaving
        // now would hide a number that is still billing.
        refresh();
      }
    } catch (caught) {
      const failure = caught instanceof ApiError ? caught : null;
      toast({
        tone: "danger",
        title: "Couldn't delete your receptionist",
        description: failure?.message ?? "Please try again.",
      });
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <Card className="border-danger/35">
        <CardHeader
          title="Delete this receptionist"
          description="Stop answering calls and remove the agent. You can choose whether to keep your phone number."
        />
        <div className="space-y-4 p-5 sm:p-6">
          {leftover && (
            <Alert tone="warn" title="Cleanup unfinished">
              {leftover}
            </Alert>
          )}
          <p className="text-sm leading-relaxed text-muted">
            Your call history, transcripts and summaries are kept. Deleting the receptionist does
            not close your account.
          </p>
          <Button variant="danger" onClick={() => setOpen(true)}>
            Delete receptionist
          </Button>
        </div>
      </Card>

      <ConfirmDialog
        open={open}
        title="Delete this receptionist?"
        confirmLabel={deleting ? "Deleting…" : "Delete receptionist"}
        busy={deleting}
        onConfirm={confirm}
        onCancel={close}
      >
        <div className="space-y-4">
          <dl className="space-y-1.5 rounded-xl bg-surface-2 px-4 py-3 text-sm">
            <div className="flex justify-between gap-4">
              <dt className="text-muted">Receptionist</dt>
              <dd className="font-medium text-ink">{tenant.name}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted">Phone number</dt>
              <dd className="font-medium text-ink tabular-nums">
                {phone ? formatPhone(phone.e164) : "None"}
              </dd>
            </div>
          </dl>

          <p>
            Your receptionist will stop handling calls straight away. Callers will hear that the
            number is no longer in service. Your call history is kept.
          </p>

          {phone && releasable && (
            <label className="flex cursor-pointer gap-3 rounded-xl border border-line p-3.5">
              <input
                type="checkbox"
                className="mt-0.5 size-4 shrink-0 accent-accent"
                checked={release}
                disabled={deleting}
                onChange={(event) => setRelease(event.target.checked)}
              />
              <span className="min-w-0 text-sm">
                <span className="font-medium text-ink">
                  Also release {formatPhone(phone.e164)}
                </span>
                <span className="mt-1 block text-muted">
                  Hands the number back to the carrier and stops its monthly rental once the
                  release takes effect. The number may be given to someone else, and usually
                  cannot be recovered.
                </span>
              </span>
            </label>
          )}

          {phone && !releasable && (
            <p className="text-sm text-muted">
              {phone.e164} was not bought through us, so we cannot release it. Your receptionist
              will stop using it; arrange any changes with whoever provides it.
            </p>
          )}

          {phone && (release ? null : (
            <p className="text-xs text-muted">
              Leaving this unticked keeps {formatPhone(phone.e164)} on your account so you can use
              it again later. Its monthly rental continues.
            </p>
          ))}
        </div>
      </ConfirmDialog>
    </>
  );
}
