import { useId, type CSSProperties } from "react";
import { posterArt, VIEWBOX, type Layer, type PosterInput, type Shape } from "../lib/poster";

/**
 * An event's generated poster: two stacked SVGs, one per ink, the top one nudged out of
 * register by CSS so the offset is in real pixels at any size. Decoration only: the event's
 * name is always real text nearby, so the art is hidden from screen readers and holds no text.
 */
export function Poster({
  event,
  size,
  index = 0,
}: {
  event: PosterInput;
  size: "thumb" | "card" | "hero";
  /** Position in a list, to stagger the inks settling on first paint. */
  index?: number;
}) {
  const art = posterArt(event);
  const halftone = `ht${useId().replace(/[^A-Za-z0-9_-]/g, "")}`;
  const style = {
    "--dx": `${art.offset.dx}px`,
    "--dy": `${art.offset.dy}px`,
    "--i": Math.min(index, 8),
  } as CSSProperties;
  return (
    <div
      className={`poster poster--${size}`}
      aria-hidden="true"
      data-motif={art.motif}
      style={style}
    >
      <Ink layer={art.layers[0]} order="a" halftone={halftone} />
      <Ink layer={art.layers[1]} order="b" halftone={halftone} />
    </div>
  );
}

function Ink({ layer, order, halftone }: { layer: Layer; order: "a" | "b"; halftone: string }) {
  const paint = layer.halftone ? `url(#${halftone})` : "currentColor";
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 ${VIEWBOX} ${VIEWBOX}`}
      preserveAspectRatio="xMidYMid slice"
      className={`poster__ink poster__ink--${order} poster__ink--${layer.ink}`}
    >
      {layer.halftone && (
        <defs>
          <pattern
            id={halftone}
            width="2.6"
            height="2.6"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <circle cx="1.3" cy="1.3" r="0.95" fill="currentColor" />
          </pattern>
        </defs>
      )}
      {layer.shapes.map((shape, i) => (
        <Mark key={i} shape={shape} paint={paint} />
      ))}
    </svg>
  );
}

function Mark({ shape, paint }: { shape: Shape; paint: string }) {
  switch (shape.kind) {
    case "circle":
      return shape.stroke ? (
        <circle
          cx={shape.cx}
          cy={shape.cy}
          r={shape.r}
          fill="none"
          stroke={paint}
          strokeWidth={shape.stroke}
        />
      ) : (
        <circle cx={shape.cx} cy={shape.cy} r={shape.r} fill={paint} />
      );
    case "rect":
      return (
        <rect
          x={shape.x}
          y={shape.y}
          width={shape.width}
          height={shape.height}
          fill={paint}
          transform={
            shape.rotate
              ? `rotate(${shape.rotate} ${shape.x + shape.width / 2} ${shape.y + shape.height / 2})`
              : undefined
          }
        />
      );
    case "polygon":
      return (
        <polygon points={shape.points.map((point) => point.join(",")).join(" ")} fill={paint} />
      );
    case "line":
      return (
        <line
          x1={shape.x1}
          y1={shape.y1}
          x2={shape.x2}
          y2={shape.y2}
          stroke={paint}
          strokeWidth={shape.stroke}
        />
      );
  }
}
