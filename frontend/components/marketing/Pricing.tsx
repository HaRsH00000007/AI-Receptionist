/**
 * Pricing, driven entirely by the plan catalog.
 *
 * No plan has a price in the codebase yet, so a `null` price renders as a
 * conversation rather than a number. Setting `price` in `lib/plans.ts` is all it
 * takes to show one here. The catalog lists only features the product ships,
 * so nothing on this page promises what does not exist.
 */

import type { ReactNode } from "react";

import { ButtonLink } from "@/components/ui/Button";
import { cx } from "@/components/ui/cx";
import { CheckIcon } from "@/components/ui/icons";
import { formatNumber } from "@/lib/format";
import {
  ALL_FEATURES,
  FEATURE_LABELS,
  PLAN_CATALOG,
  TRIAL_INCLUDED_MINUTES,
  formatPrice,
} from "@/lib/plans";

import { TALK_TO_US_HREF } from "./links";
import { SectionHeading } from "./SectionHeading";

export function Pricing() {
  return (
    <section id="pricing" aria-labelledby="pricing-title" className="py-20 sm:py-28">
      <div className="container-page">
        <SectionHeading
          id="pricing-title"
          eyebrow="Pricing"
          title="Plans that grow with your call volume"
          description={`Every new account starts with a free trial that includes ${TRIAL_INCLUDED_MINUTES} minutes, so you can hear your receptionist before you commit.`}
        />

        <ul className="mt-14 grid gap-5 lg:grid-cols-3">
          {PLAN_CATALOG.map((plan) => (
            <li
              key={plan.id}
              className={cx(
                "card relative flex flex-col p-6 sm:p-7",
                plan.recommended && "border-accent shadow-lg ring-1 ring-accent",
              )}
            >
              {plan.recommended && (
                <span className="badge badge-accent absolute -top-3 left-6">Recommended</span>
              )}
              <h3 className="text-lg font-bold tracking-tight">{plan.name}</h3>
              <p className="mt-1.5 text-sm text-muted lg:min-h-[2.5rem]">{plan.summary}</p>

              <div className="mt-6 border-b border-line pb-6">
                {plan.price ? (
                  <p className="text-3xl font-bold tracking-tight">{formatPrice(plan.price)}</p>
                ) : (
                  <>
                    <p className="text-2xl font-bold tracking-tight">Custom pricing</p>
                    <p className="mt-1 text-sm text-muted">Talk to us for a quote that fits your calls.</p>
                  </>
                )}
              </div>

              <ul className="mt-6 flex-1 space-y-3 text-sm">
                <PlanLine>{formatNumber(plan.includedMinutes)} minutes per billing period</PlanLine>
                <PlanLine>Calls keep being answered past your included minutes</PlanLine>
                {plan.features.map((feature) => (
                  <PlanLine key={feature}>{FEATURE_LABELS[feature]}</PlanLine>
                ))}
              </ul>

              <div className="mt-8 flex flex-col gap-2">
                <ButtonLink href="/get-started" variant={plan.recommended ? "primary" : "secondary"} block>
                  Get started
                </ButtonLink>
                {!plan.price && (
                  <ButtonLink href={TALK_TO_US_HREF} variant="ghost" block>
                    Talk to us
                  </ButtonLink>
                )}
              </div>
            </li>
          ))}
        </ul>

        <div className="card mt-12 overflow-x-auto">
          <table className="data-table min-w-[36rem]">
            <caption className="sr-only">Plan comparison</caption>
            <thead>
              <tr>
                <th scope="col">Compare plans</th>
                {PLAN_CATALOG.map((plan) => (
                  <th key={plan.id} scope="col" className="text-center">
                    {plan.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row" className="!bg-transparent font-medium text-ink-2">
                  Included minutes
                </th>
                {PLAN_CATALOG.map((plan) => (
                  <td key={plan.id} className="text-center font-medium">
                    {formatNumber(plan.includedMinutes)}
                  </td>
                ))}
              </tr>
              <tr>
                <th scope="row" className="!bg-transparent font-medium text-ink-2">
                  Beyond included minutes
                </th>
                {PLAN_CATALOG.map((plan) => (
                  <td key={plan.id} className="text-center text-sm text-ink-2">
                    Billed at plan rate
                  </td>
                ))}
              </tr>
              {ALL_FEATURES.map((feature) => (
                <tr key={feature}>
                  <th scope="row" className="!bg-transparent font-medium text-ink-2">
                    {FEATURE_LABELS[feature]}
                  </th>
                  {PLAN_CATALOG.map((plan) => (
                    <td key={plan.id} className="text-center">
                      {plan.features.includes(feature) ? (
                        <>
                          <CheckIcon size={16} className="inline text-success" strokeWidth={2.4} />
                          <span className="sr-only">Included</span>
                        </>
                      ) : (
                        <>
                          <span aria-hidden className="text-subtle">
                            —
                          </span>
                          <span className="sr-only">Not included</span>
                        </>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function PlanLine({ children }: { children: ReactNode }) {
  return (
    <li className="flex items-start gap-2.5 text-ink-2">
      <CheckIcon size={16} className="mt-0.5 shrink-0 text-success" strokeWidth={2.2} />
      {children}
    </li>
  );
}
