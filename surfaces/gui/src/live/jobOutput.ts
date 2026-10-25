// The live output of one background job, as the session socket streams it
// `job.follow` buffer, sized for this
// GUI.
//
// Two facts make this a module rather than a few lines in the event handler:
//
//   · **A stream can have holes.** The socket reconnects, and a chatty job outruns any
//     buffer we are willing to keep. The reference design carries a `gapBefore` flag and so do we: text that
//     does not begin where the job did says so, because a panel that silently dropped the
//     middle of a build log would be lying about the run.
//   · **The bound must be honest and cheap.** A `while true; do echo …; done` job would
//     otherwise grow this buffer for the life of the conversation. The tail is kept up to
//     `JOB_OUTPUT_LIMIT`; trimming sets `gapBefore`, and the trim is surrogate-pair safe so
//     an emoji (or any astral character) is never cut in half.

/** What one job's streamed output looks like at a point in time. */
export interface JobOutputBuffer {
  /** The most recent output kept, oldest first. */
  text: string;
  /** The job's cumulative character count at the END of `text`, as the engine reported it. */
  total: number;
  /** The text does not begin where the job did: frames were missed or trimmed away. */
  gapBefore: boolean;
  /** The engine's final flush arrived. What is here is complete from `gapBefore` onward. */
  ended: boolean;
}

/** How much of one job's output is kept in memory. The engine caps a single frame at 64 KiB
 *  and coalesces at ~100 ms, so this holds a few maximum-size frames — far more than a panel
 *  can show, and still bounded for a job that prints forever. (`RENDER_TAIL_LIMIT` is
 *  128 KiB for one row; this is per job and per conversation, so it is bigger on purpose.) */
export const JOB_OUTPUT_LIMIT = 256 * 1024;

export function emptyJobOutput(): JobOutputBuffer {
  return { text: "", total: 0, gapBefore: false, ended: false };
}

/** Cut `text` from the FRONT to `limit`, never splitting a surrogate pair. */
function tailWithin(text: string, limit: number): string {
  if (text.length <= limit) return text;
  let start = text.length - limit;
  const code = text.charCodeAt(start);
  // A low surrogate at the cut means the pair started one unit earlier: keep it whole.
  if (code >= 0xdc00 && code <= 0xdfff) start -= 1;
  return text.slice(start);
}

/**
 * Fold one streamed frame into the buffer.
 *
 * `total` is the job's cumulative character count AFTER this chunk, which is what makes the
 * fold honest in both directions:
 *   · a frame whose `total` is already covered is a duplicate (a reconnect can replay) and is
 *     dropped rather than appended twice;
 *   · a frame that skips ahead (its chunk does not start where the last one ended) marks the
 *     hole instead of closing it silently.
 *
 * A frame with no `total` (an older engine, or a hand-made frame) is appended as-is; the
 * panel then has text with an unknown position, which is exactly what it is.
 */
export function appendJobChunk(
  buffer: JobOutputBuffer,
  chunk: string,
  total?: number,
  ended = false,
): JobOutputBuffer {
  const known = typeof total === "number" && Number.isFinite(total) ? total : undefined;
  if (known !== undefined && known <= buffer.total && !ended) return buffer;
  if (chunk === "" && !ended) return buffer;
  const start = known === undefined ? buffer.total : known - chunk.length;
  // Only a frame that CARRIES a position can prove a hole; without one, the chunk is taken
  // as the next text (that is all an older engine's frame can honestly mean).
  const skipped = known !== undefined && start > buffer.total;
  const joined = buffer.text + chunk;
  const text = tailWithin(joined, JOB_OUTPUT_LIMIT);
  return {
    text,
    total: known ?? buffer.total + chunk.length,
    gapBefore: buffer.gapBefore || skipped || text.length < joined.length,
    ended: buffer.ended || ended,
  };
}

/**
 * The streamed text that comes AFTER the first `total` characters of the job — what an anchor
 * read that ended at `total` does not have yet.
 *
 * A row shows `anchor + outputAfter(buffer, anchor.total)`: the backlog from the tail route,
 * then whatever the socket has delivered beyond it. Frames that overlap the anchor are cut by
 * position, so observing before anchoring never shows a line twice. `gap` says the buffer
 * starts later than `total`: text between the anchor and the stream was never received.
 */
export function outputAfter(
  buffer: JobOutputBuffer | undefined,
  total: number,
): { text: string; gap: boolean } {
  if (!buffer || buffer.total <= total) return { text: "", gap: false };
  const needed = buffer.total - total;
  if (buffer.text.length >= needed) {
    return { text: buffer.text.slice(buffer.text.length - needed), gap: false };
  }
  return { text: buffer.text, gap: true };
}
