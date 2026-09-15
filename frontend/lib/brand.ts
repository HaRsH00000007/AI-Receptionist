/**
 * Product identity, in one place.
 *
 * The name is a working brand. Renaming the product is a change to this file
 * and nothing else — no page spells it out on its own.
 */

export const BRAND = {
  name: "Evenline",
  tagline: "The AI receptionist that answers every call",
  description:
    "An AI receptionist that answers your business calls, captures every lead and sends you a clear summary of each conversation.",
  /**
   * Where "Talk to us" and support links point. Optional: when unset, those
   * links fall back to the contact page rather than to an address nobody reads.
   */
  supportEmail: process.env.NEXT_PUBLIC_SUPPORT_EMAIL || null,
} as const;
