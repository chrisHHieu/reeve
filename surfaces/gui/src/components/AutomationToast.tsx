/** A scheduled automation run that started: a quiet panel in the top corner, the accent on
 *  its one action only, gone after 5s as the line at its foot drains. Below the title bar in a
 *  window that draws its own (`framed`). */
export function AutomationToast({ title, time, framed, onView, onDismiss }: { title: string; time: string; framed: boolean; onView: () => void; onDismiss: () => void }) {
  return (
    <div className={"fixed z-[45] w-[290px] bg-panel border border-line rounded-xl shadow-lg px-3.5 pt-3 pb-2.5 " + (framed ? "top-[56px] right-3" : "top-3 right-3")} data-testid="automation-toast">
      <div className="flex items-center gap-2 text-[13px] font-semibold">
        <span className="w-[7px] h-[7px] rounded-full bg-faint toast-pulse" />
        Automation started
      </div>
      <div className="text-[13px] text-muted mt-0.5 ml-[15px] truncate">
        {title} · {time} run
      </div>
      <div className="flex items-center justify-between ml-[15px] mt-1.5">
        <button className="text-[13px] text-accent font-medium" data-testid="toast-view-run" onClick={onView}>
          View run ›
        </button>
        <button className="text-[12px] text-faint px-0.5" data-testid="toast-dismiss" title="Dismiss" onClick={onDismiss}>
          ✕
        </button>
      </div>
      <div className="absolute left-3 right-3 bottom-1 h-[2px] rounded bg-line overflow-hidden">
        <span className="block h-full bg-faint toast-drain" />
      </div>
    </div>
  );
}
