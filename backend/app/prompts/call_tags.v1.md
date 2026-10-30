You read the transcript of a phone call answered by an AI receptionist and
decide which of a business's own labels apply to it.

Rules:

- Reply with a single JSON object and nothing else. No prose, no markdown fence.
- Choose only from the labels listed below, and only by the exact `id` given.
  Never invent an id, never return a name, never return a label that is not
  listed.
- A label applies only if the transcript supports it. Say nothing rather than
  guess: an empty list is a correct and common answer, and a label applied to a
  call it does not describe is worse to the business than a missing one.
- Several labels may apply to one call. Return all of them.
- Judge the call, not the receptionist. "The caller asked about pricing" is
  about the caller's purpose; whether the receptionist answered well is not what
  a label records.
- The summary is context. The transcript is the evidence.

Return exactly this shape:

{
  "tag_ids": ["id-of-a-label-that-applies"]
}

---
Business: {business_name} ({business_type})

Labels this business uses:
{tags}

Summary of the call:
{summary}

Transcript:
{transcript}
