// The templates of the New automation form. A template is not a second way to make an
// automation: picking one fills the one form (name, schedule, instructions) and adds the
// fields its instructions are written from (a repository, a channel).

export interface Template {
  key: string;
  title: string;
  blurb: string;
  cadence: string; // the card's last line
  conns: { name: string; label: string; why: string }[]; // [] = it needs no connection
  needsRepo?: boolean;
  needsChannel?: boolean;
  /** It writes somewhere: the form offers the standing approval for that one target. */
  consent?: boolean;
  /** It can deliver in the app or as a Slack message. */
  deliver?: boolean;
  day: string;
  time: string;
  instructions: (ctx: { repo: string; channel: string; deliver: "app" | "slack" }) => string;
}

export const TEMPLATES: Template[] = [
  {
    key: "github",
    title: "GitHub digest",
    blurb: "Merged pull requests and commits, posted to your team's Slack.",
    cadence: "Weekly",
    conns: [
      { name: "slack", label: "Slack", why: "Where the digest is posted" },
      { name: "github", label: "GitHub", why: "What the digest sums up" },
    ],
    needsRepo: true,
    needsChannel: true,
    consent: true,
    day: "mon",
    time: "09:00",
    instructions: ({ repo, channel }) =>
      `Summarize activity since the last digest in the GitHub repository ${repo || "(the connected repository)"}: ` +
      `merged pull requests, notable commits, and anything needing attention. ` +
      `Post the digest to the Slack channel ${channel || "(the channel picked above)"} using send_message.`,
  },
  {
    key: "brief",
    title: "Morning brief",
    blurb: "Calendar and unread email, summed up before your day starts.",
    cadence: "Daily",
    conns: [
      { name: "google_calendar", label: "Calendar", why: "Today's meetings and gaps" },
      { name: "gmail", label: "Gmail", why: "What arrived overnight" },
    ],
    deliver: true,
    day: "daily",
    time: "08:00",
    instructions: ({ deliver }) =>
      `Prepare a short morning brief: today's calendar events and gaps, plus email that ` +
      `arrived since yesterday evening. ` +
      (deliver === "app" ? "Save it as the session deliverable." : "Send it to me as a Slack DM."),
  },
  {
    key: "news",
    title: "Morning news briefing",
    blurb: "Five points of tech and world news, saved as Markdown.",
    cadence: "Daily",
    conns: [],
    day: "daily",
    time: "08:00",
    instructions: () =>
      "Search the web for the most important technology and world news from the last 24 hours " +
      "and write a concise 5-bullet briefing, saved as a markdown file.",
  },
  {
    key: "emaildigest",
    title: "Email digest",
    blurb: "One short digest of your unread email.",
    cadence: "Weekdays",
    conns: [{ name: "gmail", label: "Gmail", why: "Your unread email" }],
    day: "weekdays",
    time: "09:00",
    instructions: () => "Summarize my unread email into one short digest note.",
  },
  {
    key: "cleanup",
    title: "Folder cleanup",
    blurb: "Sort recent Downloads into folders by type.",
    cadence: "Weekly",
    conns: [],
    day: "fri",
    time: "17:30",
    instructions: () => "Sort my recent Downloads into tidy folders by file type.",
  },
];
