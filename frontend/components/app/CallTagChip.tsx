/**
 * A tag as it appears on a call.
 *
 * Its own component because it is rendered in three places — the calls list, the
 * overview's compact list and the detail drawer — and the colour has to come
 * from the palette variable rather than a Tailwind class: the set of colours is
 * data from the server, so there is no class name to write at build time.
 *
 * The dot carries the colour and the text carries the meaning, so the chip is
 * still readable to someone who cannot distinguish the eight hues.
 */

import { TagDot } from "@/components/app/agent/AgentTags";
import type { CallTagLabel } from "@/lib/types";

export function CallTagChip({ tag }: { tag: CallTagLabel }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2 py-0.5 text-xs font-medium text-ink-2">
      <TagDot color={tag.color} size={7} />
      {tag.name}
    </span>
  );
}
