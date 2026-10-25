// The markdown subtree's seatbelt.
//
// Markdown is the one renderer that eats arbitrary model output, and a throw anywhere in it
// (a grammar, a KaTeX macro, a plugin) would otherwise take the whole transcript down with a
// white screen. The honest degradation is the raw text: wrong-looking, but readable, local,
// and it says what the model actually wrote.
//
// One boundary per top-level block (rather than one for the whole message) so a single bad
// block costs that block and not the answer around it. The reset key is the block's own text:
// a failure while streaming is not a verdict on the finished message, and the next token
// retries.

import { Component, type ReactNode } from "react";

export class MarkdownBoundary extends Component<
  { text: string; children: ReactNode },
  { failedFor: string | null }
> {
  state: { failedFor: string | null } = { failedFor: null };

  // Marker only: `getDerivedStateFromError` cannot see props, so `componentDidCatch` records
  // which text failed and the reset rule can compare against it.
  static getDerivedStateFromError(): { failedFor: string } {
    return { failedFor: "" };
  }

  static getDerivedStateFromProps(
    props: { text: string },
    state: { failedFor: string | null },
  ): { failedFor: null } | null {
    if (state.failedFor !== null && state.failedFor !== "" && state.failedFor !== props.text) {
      return { failedFor: null };
    }
    return null;
  }

  componentDidCatch(error: unknown) {
    console.error("[Markdown] block failed to render; showing its raw text", error);
    this.setState({ failedFor: this.props.text });
  }

  render() {
    if (this.state.failedFor !== null) {
      return (
        <pre className="md-fallback" data-testid="markdown-fallback">
          {this.props.text}
        </pre>
      );
    }
    return this.props.children;
  }
}
