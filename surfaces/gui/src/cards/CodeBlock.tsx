// A standalone code block: a program about to run, or a patch envelope.
//
// The `code` tool-call variant and the details panel both need this, and neither can use
// `ReadBlock` — there is no file and therefore no line numbers to keep.
//
// The highlight is applied to the TRIMMED text (one trailing newline removed), because a
// terminal newline would otherwise render as a blank last row; and `lang` is optional, in
// which case the block is plain monospace. A language id the app cannot load behaves the
// same way, which is why `highlightLines` returns undefined rather than throwing.

import { useMemo } from "react";
import { CopyButton } from "../components/primitives/CopyButton";
import { FileIcon } from "../components/FileIcon";
import { highlightLines, useGrammars } from "../components/primitives/highlight";
import { ScrollFade } from "../components/primitives/ScrollFade";

export function CodeBlock({
  code,
  lang,
  label,
  maxHeight = 224,
}: {
  code: string;
  lang?: string;
  label?: string;
  maxHeight?: number;
}) {
  const trimmed = useMemo(
    () => (code.endsWith("\n") ? code.slice(0, -1) : code),
    [code],
  );
  const grammars = useGrammars(lang);
  const spans = useMemo(() => highlightLines(trimmed, lang), [trimmed, lang, grammars]);
  return (
    <div className="rv-card" data-code="">
      <div className="rv-banner">
        <FileIcon lang={lang} />
        <span className="rv-banner-label">{label ?? lang ?? "Code"}</span>
        <span className="rv-banner-spacer" />
        <CopyButton text={trimmed} />
      </div>
      <ScrollFade className="rv-body rv-nowrap rv-scroll" style={{ maxHeight, overflowY: "auto" }}>
        {spans
          ? spans.map((line, index) => (
              <div key={index} className="rv-term-line">
                {line.length === 0 ? (
                  "\u00a0"
                ) : (
                  line.map((span, spanIndex) => (
                    <span key={spanIndex} style={span.style}>
                      {span.text}
                    </span>
                  ))
                )}
              </div>
            ))
          : trimmed.split("\n").map((line, index) => (
              <div key={index} className="rv-term-line">
                {line || "\u00a0"}
              </div>
            ))}
      </ScrollFade>
    </div>
  );
}
