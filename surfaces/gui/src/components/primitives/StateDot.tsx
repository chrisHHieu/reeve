// A status dot, and the one moving state among them.
//
// including the part that is easy to get wrong: `ongoing` is not a CSS spinner. It is an
// 8-cell chase around a 3x3 pixel matrix, drawn as SVG rects with a per-cell animation
// delay, so the loop starts from the top-left every time regardless of when the component
// mounted. A CSS animation on a rotating ring would instead start mid-rotation, and the
// same dot would look different in two rows.
//
// Every instance is `aria-hidden`: a dot is a colour, and colour alone is not a channel.
// Callers pair it with a visually hidden word (see `ToolRow`'s status label).

export type StateDotState = "done" | "warning" | "ongoing" | "error";

// Clockwise from the top-left of a 3x3 grid, skipping the centre. Cells are 2px on a
// 10px grid, so the chase reads as a pixel-art ring rather than a smooth disc.
const MATRIX_CELLS: ReadonlyArray<readonly [number, number]> = [
  [0, 0],
  [1, 0],
  [2, 0],
  [2, 1],
  [2, 2],
  [1, 2],
  [0, 2],
  [0, 1],
];

const CELL = 2;
const SIZE = 10;

export function StateDot({
  state,
  size = SIZE,
  className,
}: {
  state: StateDotState;
  size?: number;
  className?: string;
}) {
  if (state === "ongoing") {
    return (
      <svg
        aria-hidden="true"
        width={size}
        height={size}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        className={className}
        style={{ shapeRendering: "crispEdges" }}
      >
        {MATRIX_CELLS.map(([col, row], index) => (
          <rect
            key={`${col}-${row}`}
            x={col * (SIZE / 3) + 1}
            y={row * (SIZE / 3) + 1}
            width={CELL}
            height={CELL}
            className="state-dot-cell"
            // Negative delays, so every cell is already mid-cycle on mount and the ring
            // is full from the first frame instead of filling up over one revolution.
            style={{ animationDelay: `${(index - MATRIX_CELLS.length) * 125}ms` }}
          />
        ))}
      </svg>
    );
  }
  return (
    <span
      aria-hidden="true"
      className={"state-dot" + (className ? ` ${className}` : "")}
      data-state={state}
      data-testid={`state-dot-${state}`}
      style={{ width: size, height: size }}
    />
  );
}
