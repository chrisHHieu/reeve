// Mark, OpenDesign's draw tool (read 2026-10-19): a box, a stroke or a few words drawn
// over the design, with a note, sent as a picture of the area with the ink on it and the parts under
// it. The ink is the app's, over the frame, so the page is never touched; the picture is taken by
// Reeve's own browser (the frame is another origin, so its pixels cannot be read here).
//
// Shapes are kept in the page's window px (the frame's px divided by its scale), the same px the
// page reports and the picture is shot in.
import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Icon } from "../Icon";

export type MarkShape =
  | { kind: "box"; x: number; y: number; w: number; h: number }
  | { kind: "pen"; points: [number, number][] }
  | { kind: "text"; x: number; y: number; text: string };

export type MarkTool = "box" | "pen" | "text";

/** The ink's colour: one that stands apart from a design's own. */
export const INK = "#d9480f";
const PAD = 16;

/** The area the shapes cover, with room around them, in window px, never above or left of zero. */
export function markBounds(shapes: MarkShape[]): { x: number; y: number; w: number; h: number } | null {
  const xs: number[] = [];
  const ys: number[] = [];
  for (const shape of shapes) {
    if (shape.kind === "box") {
      xs.push(shape.x, shape.x + shape.w);
      ys.push(shape.y, shape.y + shape.h);
    } else if (shape.kind === "pen") {
      for (const [x, y] of shape.points) {
        xs.push(x);
        ys.push(y);
      }
    } else {
      xs.push(shape.x, shape.x + Math.max(40, shape.text.length * 8));
      ys.push(shape.y - 16, shape.y + 4);
    }
  }
  if (!xs.length) return null;
  const x = Math.max(0, Math.floor(Math.min(...xs) - PAD));
  const y = Math.max(0, Math.floor(Math.min(...ys) - PAD));
  return { x, y, w: Math.ceil(Math.max(...xs) + PAD) - x, h: Math.ceil(Math.max(...ys) + PAD) - y };
}

/** Draw the shapes onto the picture of their area. Null when the canvas cannot draw (the picture is
 *  then sent as it is). */
export async function inkOver(png: string, shapes: MarkShape[], area: { x: number; y: number }): Promise<string | null> {
  try {
    const image = new Image();
    image.src = png;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const ink = canvas.getContext("2d");
    if (!ink) return null;
    ink.drawImage(image, 0, 0);
    ink.translate(-area.x, -area.y);
    ink.strokeStyle = INK;
    ink.fillStyle = INK;
    ink.lineWidth = 3;
    ink.lineCap = "round";
    ink.lineJoin = "round";
    ink.font = "600 15px system-ui, sans-serif";
    for (const shape of shapes) {
      if (shape.kind === "box") ink.strokeRect(shape.x, shape.y, shape.w, shape.h);
      else if (shape.kind === "pen" && shape.points.length > 1) {
        ink.beginPath();
        ink.moveTo(...shape.points[0]);
        for (const point of shape.points.slice(1)) ink.lineTo(...point);
        ink.stroke();
      } else if (shape.kind === "text") ink.fillText(shape.text, shape.x, shape.y);
    }
    return canvas.toDataURL("image/png");
  } catch {
    return null;
  }
}

export function MarkLayer({
  left,
  width,
  height,
  scale,
  busy,
  onSend,
}: {
  /** Where the frame is drawn in the stage, and how big, in screen px. */
  left: number;
  width: number;
  height: number;
  /** Screen px per window px. */
  scale: number;
  busy: boolean;
  onSend: (shapes: MarkShape[], note: string) => void;
}) {
  const [tool, setTool] = useState<MarkTool>("box");
  const [shapes, setShapes] = useState<MarkShape[]>([]);
  const [note, setNote] = useState("");
  const [typing, setTyping] = useState<{ x: number; y: number; text: string } | null>(null);
  const drawing = useRef<MarkShape | null>(null);
  const [live, setLive] = useState<MarkShape | null>(null);
  const k = scale > 0 ? scale : 1;
  const at = (event: ReactPointerEvent) => {
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    return [Math.round((event.clientX - box.left) / k), Math.round((event.clientY - box.top) / k)] as [number, number];
  };
  const down = (event: ReactPointerEvent) => {
    if (busy) return;
    const [x, y] = at(event);
    if (tool === "text") {
      setTyping({ x, y, text: "" });
      return;
    }
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
    drawing.current = tool === "box" ? { kind: "box", x, y, w: 0, h: 0 } : { kind: "pen", points: [[x, y]] };
    setLive(drawing.current);
  };
  const move = (event: ReactPointerEvent) => {
    const shape = drawing.current;
    if (!shape) return;
    const [x, y] = at(event);
    const next: MarkShape =
      shape.kind === "box"
        ? { ...shape, w: x - shape.x, h: y - shape.y }
        : shape.kind === "pen"
          ? { kind: "pen", points: [...shape.points, [x, y]] }
          : shape;
    drawing.current = next;
    setLive(next);
  };
  const up = () => {
    const shape = drawing.current;
    drawing.current = null;
    setLive(null);
    if (!shape) return;
    if (shape.kind === "box") {
      const box = { kind: "box" as const, x: Math.min(shape.x, shape.x + shape.w), y: Math.min(shape.y, shape.y + shape.h), w: Math.abs(shape.w), h: Math.abs(shape.h) };
      if (box.w >= 6 && box.h >= 6) setShapes((current) => [...current, box]);
    } else if (shape.kind === "pen" && shape.points.length > 2) setShapes((current) => [...current, shape]);
  };
  const draw = (shape: MarkShape, index: number | string) =>
    shape.kind === "box" ? (
      <rect key={index} x={shape.x} y={shape.y} width={Math.abs(shape.w)} height={Math.abs(shape.h)} rx={4} />
    ) : shape.kind === "pen" ? (
      <polyline key={index} points={shape.points.map((p) => p.join(",")).join(" ")} />
    ) : (
      <text key={index} x={shape.x} y={shape.y}>
        {shape.text}
      </text>
    );
  const tools: { id: MarkTool; label: string; icon: "square" | "pencilLine" | "type" }[] = [
    { id: "box", label: "Box", icon: "square" },
    { id: "pen", label: "Pen", icon: "pencilLine" },
    { id: "text", label: "Words", icon: "type" },
  ];
  return (
    <>
      <div
        className="dv-mark"
        data-testid="design-mark-layer"
        style={{ left, width, height }}
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
      >
        <svg width={width} height={height} viewBox={`0 0 ${width / k} ${height / k}`} aria-hidden="true">
          <g fill="none" stroke={INK} strokeWidth={3 / k > 1.5 ? 3 : 3 / k} strokeLinecap="round" strokeLinejoin="round">
            {shapes.filter((s) => s.kind !== "text").map(draw)}
            {live && live.kind !== "text" && draw(live, "live")}
          </g>
          <g fill={INK} fontSize={15} fontWeight={600} fontFamily="system-ui, sans-serif">
            {shapes.filter((s) => s.kind === "text").map(draw)}
          </g>
        </svg>
        {typing && (
          <input
            className="dv-mark-words"
            autoFocus
            aria-label="Words to put on the page"
            placeholder="Words…"
            value={typing.text}
            style={{ left: typing.x * k, top: typing.y * k - 14 }}
            onPointerDown={(event) => event.stopPropagation()}
            onChange={(event) => setTyping({ ...typing, text: event.target.value.slice(0, 120) })}
            onKeyDown={(event) => {
              if (event.key === "Enter" && typing.text.trim()) {
                setShapes((current) => [...current, { kind: "text", x: typing.x, y: typing.y, text: typing.text.trim() }]);
                setTyping(null);
              } else if (event.key === "Escape") setTyping(null);
            }}
            onBlur={() => {
              if (typing.text.trim()) setShapes((current) => [...current, { kind: "text", x: typing.x, y: typing.y, text: typing.text.trim() }]);
              setTyping(null);
            }}
          />
        )}
      </div>
      <div className="dv-mark-dock" role="toolbar" aria-label="Mark" data-testid="design-mark-dock">
        {tools.map((option) => (
          <button
            key={option.id}
            type="button"
            className={"artifact-icon-btn" + (tool === option.id ? " on" : "")}
            aria-pressed={tool === option.id}
            aria-label={option.label}
            title={option.label}
            data-testid={`design-mark-${option.id}`}
            onClick={() => setTool(option.id)}
          >
            <Icon name={option.icon} size={15} />
          </button>
        ))}
        <span className="dv-tools-rule" aria-hidden="true" />
        <input
          className="dv-mark-note"
          aria-label="A note for the mark"
          placeholder="Say what should change here"
          value={note}
          data-testid="design-mark-note"
          onChange={(event) => setNote(event.target.value.slice(0, 1000))}
        />
        <button type="button" className="fv-note-btn" disabled={!shapes.length || busy} onClick={() => setShapes([])}>
          Clear
        </button>
        <button
          type="button"
          className="btn sm primary"
          disabled={!shapes.length || busy}
          data-testid="design-mark-send"
          onClick={() => onSend(shapes, note)}
        >
          {busy ? "Taking the picture…" : "Add to message"}
        </button>
      </div>
    </>
  );
}
