// A tool row's seatbelt, the transcript's counterpart of `MarkdownBoundary`.
//
// A tool row draws data nobody here controls: arguments a model wrote and results a tool, a
// connector or an MCP server returned. A throw anywhere under it used to unmount the whole
// app, and since the row is drawn again on every reload, the session that held it could not be
// opened at all (one `goal` result with a null field, session 14715fae-61f, 2026-10-04).
//
// The backend's rule for the same data is "tool contracts degrade, never break". This is that
// rule on the screen: a row that cannot be drawn costs that row. The fallback names the call so
// the reader knows what is missing, and says the call itself is not what failed.
//
// The reset key is whatever identifies the row's content (call id + state): a row that failed
// while running is drawn again when it settles.

import { Component, type ReactNode } from "react";

export class RowBoundary extends Component<
  { resetKey: string; title: string; children: ReactNode },
  { failedFor: string | null }
> {
  state: { failedFor: string | null } = { failedFor: null };

  // Marker only: `getDerivedStateFromError` cannot see props, so `componentDidCatch` records
  // which content failed and the reset rule compares against it.
  static getDerivedStateFromError(): { failedFor: string } {
    return { failedFor: "" };
  }

  static getDerivedStateFromProps(
    props: { resetKey: string },
    state: { failedFor: string | null },
  ): { failedFor: null } | null {
    if (state.failedFor !== null && state.failedFor !== "" && state.failedFor !== props.resetKey) {
      return { failedFor: null };
    }
    return null;
  }

  componentDidCatch(error: unknown) {
    console.error(`[ToolRow] "${this.props.title}" failed to render; showing a plain row`, error);
    this.setState({ failedFor: this.props.resetKey });
  }

  render() {
    if (this.state.failedFor !== null) {
      return (
        <div className="tool-row" data-testid="tool-row-fallback">
          <div className="tool-row-line">
            <span className="tool-row-title">{this.props.title}</span>
            <span className="tool-row-undrawn">
              This row could not be drawn. The call itself is not affected.
            </span>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
