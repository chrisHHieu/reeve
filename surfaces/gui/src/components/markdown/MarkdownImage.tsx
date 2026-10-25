// Model-emitted images, a run of images becomes ONE gallery block
// and every picture opens into a lightbox that can step through the run, zoom, download, or
// hand the original to the browser (`ImagePreviewDialog` in miniature).
//
// Two rules that make it behave:
//
//   An image inside a LINK stays a plain image: a zoom button inside an anchor is invalid
//   HTML and the browser silently drops one of the two behaviours. The `InsideLink` context
//   (shared with the inline-code renderer's anti-nesting rule) is what tells them apart.
//
//   The overlay is a portal, so a card's `overflow: hidden` cannot clip it, and a plain
//   `<div>` rather than a focus-trap library: the image is not a modal form, and over-trapping
//   is its own accessibility bug. Escape and a click outside close it; arrows step.

import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export interface MarkdownImageRef {
  src: string;
  alt?: string;
}

export function MarkdownImage({
  src,
  alt,
  inLink,
  gallery,
  index = 0,
}: {
  src?: string;
  alt?: string;
  inLink: boolean;
  /** The run of images this one belongs to (consecutive image-only lines). */
  gallery?: MarkdownImageRef[];
  index?: number;
}) {
  const [open, setOpen] = useState(false);
  const image = <img className="md-img" src={src} alt={alt ?? ""} loading="lazy" />;
  if (!src || inLink) return image;
  const run = gallery && gallery.length > 1 ? gallery : [{ src, alt }];
  const at = Math.min(index, run.length - 1);

  return (
    <>
      <button
        type="button"
        className={run.length > 1 ? "md-imgthumb" : "md-imgwrap"}
        data-testid="md-image-open"
        title={alt ? `${alt} — click to enlarge` : "Click to enlarge"}
        onClick={() => setOpen(true)}
      >
        {image}
      </button>
      {open && (
        <ImageOverlay images={run} start={at} onClose={() => setOpen(false)} />
      )}
    </>
  );
}

/** A run of image-only lines, rendered as a responsive gallery. */
export function MarkdownImageGallery({ images }: { images: MarkdownImageRef[] }) {
  const [openAt, setOpenAt] = useState<number | null>(null);
  return (
    <div className="md-img-gallery" data-testid="md-image-gallery">
      {images.map((image, index) => (
        <button
          key={`${image.src}-${index}`}
          type="button"
          className="md-imgthumb"
          data-testid="md-image-open"
          title={image.alt ? `${image.alt} — click to enlarge` : "Click to enlarge"}
          onClick={() => setOpenAt(index)}
        >
          <img className="md-img" src={image.src} alt={image.alt ?? ""} loading="lazy" />
        </button>
      ))}
      {openAt !== null && (
        <ImageOverlay images={images} start={openAt} onClose={() => setOpenAt(null)} />
      )}
    </div>
  );
}

export function ImageOverlay({
  images,
  start,
  onClose,
}: {
  images: MarkdownImageRef[];
  start: number;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(start);
  const [zoom, setZoom] = useState(1);
  const current = images[index];
  const many = images.length > 1;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (!many) return;
      if (event.key === "ArrowRight") setIndex((i) => (i + 1) % images.length);
      if (event.key === "ArrowLeft") setIndex((i) => (i - 1 + images.length) % images.length);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [images.length, many, onClose]);

  // A different picture is a different picture: the zoom does not carry over.
  useEffect(() => setZoom(1), [index]);

  const download = () => {
    const link = document.createElement("a");
    link.href = current.src;
    link.download = (current.alt || current.src.split("/").pop() || "image").slice(0, 80);
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  return createPortal(
    <div
      className="md-img-overlay"
      data-testid="md-image-viewer"
      role="dialog"
      aria-label={current.alt || "Image"}
      onClick={onClose}
    >
      <div className="md-img-stage" onClick={(event) => event.stopPropagation()}>
        {many && (
          <button
            type="button"
            className="md-img-step"
            data-testid="md-image-prev"
            aria-label="Previous image"
            onClick={() => setIndex((i) => (i - 1 + images.length) % images.length)}
          >
            ‹
          </button>
        )}
        <img
          className="md-img-full"
          src={current.src}
          alt={current.alt ?? ""}
          style={{ transform: `scale(${zoom})` }}
          draggable={false}
        />
        {many && (
          <button
            type="button"
            className="md-img-step"
            data-testid="md-image-next"
            aria-label="Next image"
            onClick={() => setIndex((i) => (i + 1) % images.length)}
          >
            ›
          </button>
        )}
      </div>
      <div className="md-img-bar" onClick={(event) => event.stopPropagation()}>
        <button type="button" onClick={() => setZoom((z) => Math.max(0.25, z * 0.8))} aria-label="Zoom out">
          −
        </button>
        <span data-testid="md-image-zoom">{Math.round(zoom * 100)}%</span>
        <button type="button" onClick={() => setZoom((z) => Math.min(8, z * 1.25))} aria-label="Zoom in">
          +
        </button>
        <button type="button" onClick={() => setZoom(1)}>
          Reset
        </button>
        {many && <span data-testid="md-image-count">{index + 1} / {images.length}</span>}
        <button type="button" onClick={download} data-testid="md-image-download">
          Download
        </button>
        {/* A picture held in the page itself (a data: URL: an attachment, a pasted image) has no
            original to open, and the desktop app refuses to open one in a new window. */}
        {!current.src.startsWith("data:") && (
          <a href={current.src} target="_blank" rel="noopener noreferrer">
            Open original ↗
          </a>
        )}
        <button type="button" onClick={onClose} data-testid="md-image-close">
          Close
        </button>
      </div>
    </div>,
    document.body,
  );
}

/** What the `img` renderer needs from the caller: whether it sits inside a link. */
export type ImageRenderer = (props: { src?: string; alt?: string; children?: ReactNode }) => ReactNode;
