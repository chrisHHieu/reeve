// What a conversation waits on the person for, by the kind of the prompt it holds. One list:
// the sidebar's "Needs you" rows and the Automations page say it with the same word.

export const NEEDS: Record<string, string> = {
  approval: "Approval",
  question: "Question",
  plan: "Plan",
  directory: "Folder access",
  tool: "Tool request",
};
