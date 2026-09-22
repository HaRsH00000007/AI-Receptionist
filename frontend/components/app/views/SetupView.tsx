"use client";

/**
 * The signed-in setup page: where a business is sent until its receptionist is
 * live.
 *
 * Four situations, one page. Setting up shows live progress; waiting on billing
 * says what is needed; a failed setup explains itself and — for an owner or
 * admin — offers to try again; an inactive receptionist says so and points to
 * support. The workspace polls while setup is moving, and the frame moves the
 * customer to their dashboard the moment it goes live, so nobody has to know
 * to reload.
 */

import { useState } from "react";

import { ProvisioningTimeline } from "@/components/app/ProvisioningTimeline";
import { SupportButton } from "@/components/app/SupportButton";
import { useCanManage, useTenantData } from "@/components/app/TenantWorkspace";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { ProgressBar } from "@/components/ui/Feedback";
import { RefreshIcon } from "@/components/ui/icons";
import { useToast } from "@/components/ui/Toast";
import { ApiError, retryProvisioning } from "@/lib/api";
import { formatPhone } from "@/lib/format";
import { billingReason, friendlyError, receptionistState, stepLabel } from "@/lib/status";

export function SetupView() {
  const { tenant, provisioning, phone, refresh, tenantId } = useTenantData();
  const canManage = useCanManage();
  const toast = useToast();
  const [retrying, setRetrying] = useState(false);

  const state = receptionistState(tenant.status, provisioning.status);
  const friendly = friendlyError(provisioning.last_error);
  // Only a run that stopped on an error can be retried by its owner. A rolled
  // back run released its number; restarting it is a decision for support.
  const retryable = provisioning.status === "failed";
  const running = provisioning.steps.find((step) => step.status === "running");

  async function retry() {
    setRetrying(true);
    try {
      await retryProvisioning(tenantId);
      toast({ tone: "success", title: "Setup restarted", description: "We'll pick up where it stopped." });
      refresh();
    } catch (caught) {
      toast({
        tone: "danger",
        title: "Couldn't restart setup",
        description: caught instanceof ApiError ? caught.message : "Please try again in a moment.",
      });
    } finally {
      setRetrying(false);
    }
  }

  const heading = {
    setting_up: "Setting up your receptionist",
    billing: "Setup is waiting on your plan",
    attention: "Setup needs attention",
    inactive: "This receptionist isn't active",
    live: "Your receptionist is live",
  }[state];

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm font-semibold text-muted">{tenant.name}</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight sm:text-[1.75rem]">{heading}</h1>
        {state === "setting_up" && (
          <p className="mt-2 max-w-2xl text-[0.9375rem] text-muted">
            This usually takes a few minutes. You can leave this page open — your dashboard opens
            as soon as your receptionist is answering calls.
          </p>
        )}
      </div>

      {state === "billing" && (
        <Alert tone="warn" title="Waiting for your subscription" action={<SupportButton size="sm" />}>
          {billingReason(provisioning.billing?.reason)} Setup continues automatically once your plan
          is active.
        </Alert>
      )}

      {state === "attention" && (
        <Alert
          tone="danger"
          title={friendly.title}
          action={
            retryable && canManage ? (
              <Button
                size="sm"
                variant="secondary"
                loading={retrying}
                leadingIcon={<RefreshIcon size={15} />}
                onClick={() => void retry()}
              >
                Try again
              </Button>
            ) : undefined
          }
        >
          <p>{friendly.explanation}</p>
          <p className="mt-1">
            {retryable
              ? canManage
                ? "Trying again is safe: nothing is bought twice."
                : "Ask the owner of this account to try again, or contact support."
              : "Our team needs to look at this before setup can continue."}{" "}
            Reference <span className="font-mono">{provisioning.correlation_id}</span>.
          </p>
        </Alert>
      )}

      {state === "inactive" && (
        <Card className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted">
            This receptionist was cancelled and no longer answers calls. You can set up a new one,
            or contact us if this looks wrong.
          </p>
          <div className="flex shrink-0 flex-wrap gap-2">
            <SupportButton size="sm" />
            <ButtonLink href="/get-started?another=1" size="sm">
              Set up a receptionist
            </ButtonLink>
          </div>
        </Card>
      )}

      <Card>
        <CardHeader
          title="Setup progress"
          description={`${provisioning.completed_steps} of ${provisioning.total_steps} steps complete`}
          action={
            state === "setting_up" ? (
              <Badge tone="accent" dot pulse>
                {running ? stepLabel(running.step_name) : "Updating live"}
              </Badge>
            ) : state === "billing" ? (
              <Badge tone="warn">Paused</Badge>
            ) : state === "attention" ? (
              <Badge tone="danger">Stopped</Badge>
            ) : undefined
          }
        />
        <div className="px-5 py-6">
          <ProgressBar
            value={provisioning.completed_steps}
            max={provisioning.total_steps}
            label="Setup progress"
            tone={state === "attention" ? "danger" : state === "billing" ? "warn" : "accent"}
            className="mb-6"
          />
          <ProvisioningTimeline provisioning={provisioning} />
        </div>
      </Card>

      {phone && (
        <Card className="p-5">
          <p className="text-sm text-muted">Your receptionist&apos;s number</p>
          <p className="mt-1 text-xl font-bold tracking-tight tabular-nums">{formatPhone(phone.e164)}</p>
        </Card>
      )}

      {state !== "inactive" && (
        <div className="flex flex-col gap-3 rounded-xl border border-line px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted">
            Questions while you wait? Our team can see exactly where your setup is.
          </p>
          <SupportButton size="sm" />
        </div>
      )}
    </div>
  );
}
