// The shell every full-window page shares: Automations, Metrics, Activity, Connectors,
// the agent's page.
//
// Seven pages used to hand-roll the same thing and had drifted apart: a darker background than
// the conversation and Settings, a 20px title where Settings had 28px, three different column
// widths and paddings. A reader moving between them felt each as another product. One shell
// fixes the contract: the reading sheet's background (`--canvas`), one title scale
// (`--fs-page`), one column (`--page-w`, a wider one for data tables), padding that follows the
// window (see `.page-inner` in ui.css), and the drag strip the frameless window needs.
import type { ReactNode } from "react";

export function PageShell({
  children,
  wide = false,
  nav,
  testId,
}: {
  children: ReactNode;
  /** A page of tables (Metrics) takes the wide column. */
  wide?: boolean;
  /** A page's own side navigation, left of the content. */
  nav?: ReactNode;
  testId?: string;
}) {
  return (
    <main className="page" data-testid={testId}>
      <div data-drag-region className="shell-drag" />
      <div className="page-body">
        {nav}
        <div className="page-scroll hairline-scroll">
          <div className="page-inner" data-wide={wide || undefined}>
            {children}
          </div>
        </div>
      </div>
    </main>
  );
}

export function PageHeader({
  title,
  sub,
  icon,
  actions,
}: {
  title: ReactNode;
  sub?: ReactNode;
  icon?: ReactNode;
  /** Controls on the right (a range switch, a primary button). */
  actions?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div className="page-head-copy">
        <h1 className="page-title">
          {icon}
          {title}
        </h1>
        {sub ? <p className="page-sub">{sub}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}
