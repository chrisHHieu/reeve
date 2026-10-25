// What a proposed plan is called and how long it is, read from its own markdown. The row in
// the transcript and the card that asks about it name the plan the same way.

const clean = (line: string) => line.replace(/[*_`]/g, "").trim();

/** The plan's own title: its first heading, else its first line without its list mark (a plan
 *  with no heading is named by its first step, and "1. " is not part of the name). */
export function planTitle(plan: string): string {
  const lines = (plan || "").split("\n").map((line) => line.trim()).filter(Boolean);
  const heading = lines.find((line) => /^#{1,6}\s/.test(line));
  if (heading) return clean(heading.replace(/^#{1,6}\s+/, ""));
  return clean((lines[0] ?? "").replace(/^(?:\d+[.)]|[-*+])\s+(?:\[[ xX]\]\s+)?/, ""));
}

const NUMBERED = /^\d+[.)]\s+(?:\[[ xX]\]\s+)?(.*)$/;
const STEP = /^(?:\d+[.)]|[-*+])\s+(?:\[[ xX]\]\s+)?(.*)$/;

/** The plan's steps: its numbered items when it numbers them (a bullet list beside them, such
 *  as its risks, is not steps), else its top-level list items, else its headings. */
export function planSteps(plan: string): string[] {
  const lines = (plan || "").split("\n");
  const numbered = lines.map((line) => NUMBERED.exec(line)?.[1]).filter((text): text is string => !!text);
  if (numbered.length) return numbered.map(clean).filter(Boolean);
  const listed = lines.map((line) => STEP.exec(line)?.[1]).filter((text): text is string => !!text);
  if (listed.length) return listed.map(clean).filter(Boolean);
  const headings = lines
    .map((line) => line.trim())
    .filter((line) => /^#{2,6}\s/.test(line))
    .map((line) => clean(line.replace(/^#{2,6}\s+/, "")));
  return headings.filter((text) => text && text !== planTitle(plan));
}

/** "6 steps", or nothing for a plan with no steps to count. */
export function planStepCount(plan: string): string {
  const count = planSteps(plan).length;
  return count ? `${count} ${count === 1 ? "step" : "steps"}` : "";
}
