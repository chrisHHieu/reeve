// Web results: a provider answer over a citation list, or one fetched page (FetchCard.tsx).
//
// Two
// decisions are the reason this is a component rather than a loop:
//
//   `safeHref` (webMarks.tsx) links ONLY `http:` and `https:`; anything else renders as text.
//
//   A search result is a ROW a reader scans, not a block they read (2026-10-05, on real Exa
//   results: one 900-character snippet filled the old 240px box, so a five-result search
//   showed one). Each row is the site mark, the title, where it is from (host · byline ·
//   when), and two lines of the snippet with the query's whole words marked; the rest of the
//   snippet is one click away on the row. The list scrolls past a bound, and says so with a
//   fade, so a card's height does not depend on how chatty a provider was.
//
//   What a list of links cannot say, the card says above it: which service answered, and
//   that it was the fallback when the one expected did not; which searches were merged; what
//   the filters removed and which queries failed.

import { useMemo, useState } from "react";
import type { WebResultCard, WebSource } from "./types";
import { CopyButton } from "../components/primitives/CopyButton";
import { ScrollFade } from "../components/primitives/ScrollFade";
import { Markdown } from "../components/Markdown";
import {
  SNIPPET_CLAMP_CHARS,
  hostOf,
  markTerms,
  providerLabel,
  publishedLabel,
  queryTerms,
} from "./webSearchView";
import { SourceFav, safeHref } from "./webMarks";
import { FetchCard } from "./FetchCard";

/** The label for a source: the provider's title, else the host, else the raw URL. */
function linkLabel(source: WebSource): string {
  if (source.title) return source.title;
  try {
    return new URL(source.url).hostname || source.url;
  } catch {
    return source.url;
  }
}

export function WebBlock({ card }: { card: WebResultCard }) {
  if (card.kind === "fetch") return <FetchCard card={card} />;
  return <SearchCard card={card} />;
}

function Marked({ text, terms }: { text: string; terms: readonly string[] }) {
  return (
    <>
      {markTerms(text, terms).map((run, index) =>
        run.hit ? (
          <mark key={index} className="web-hit">
            {run.text}
          </mark>
        ) : (
          run.text
        ),
      )}
    </>
  );
}

function ResultRow({
  source,
  index,
  terms,
}: {
  source: WebSource;
  index: number;
  terms: readonly string[];
}) {
  const [open, setOpen] = useState(false);
  const href = safeHref(source.url);
  const when = publishedLabel(source.published_at);
  const snippet = (source.snippet ?? "").trim();
  const long = snippet.length > SNIPPET_CLAMP_CHARS;
  return (
    // `value` pins the citation number to the source's position, so the numbers in an answer
    // line up with the list even when <ol> would start counting somewhere else.
    <li className="web-result" value={index + 1} data-open={open || undefined} data-testid="web-result">
      <SourceFav url={source.url} icon={source.icon} />
      <div className="web-result-main">
        <div className="web-result-head">
          {href ? (
            <a className="web-result-title" href={href} target="_blank" rel="noreferrer" title={source.url}>
              {linkLabel(source)}
            </a>
          ) : (
            <span className="web-result-title">{linkLabel(source)}</span>
          )}
          {when.text && (
            <time className="web-result-when" dateTime={source.published_at} title={when.title}>
              {when.text}
            </time>
          )}
        </div>
        <div className="web-result-meta">
          <span className="web-result-host">{hostOf(source.url)}</span>
          {source.author && <span className="web-result-author">{source.author}</span>}
        </div>
        {snippet &&
          (long ? (
            // A long snippet is its own disclosure: the two lines a row shows are the button,
            // so reading on costs no extra line of chrome under every result.
            <button
              type="button"
              className="web-result-snippet"
              data-clamped={open ? undefined : ""}
              aria-expanded={open}
              title={open ? "Show less" : "Show the whole snippet"}
              data-testid="web-snippet"
              onClick={() => setOpen((value) => !value)}
            >
              <Marked text={snippet} terms={terms} />
            </button>
          ) : (
            <p className="web-result-snippet" data-testid="web-snippet">
              <Marked text={snippet} terms={terms} />
            </p>
          ))}
      </div>
    </li>
  );
}

function SearchCard({ card }: { card: Extract<WebResultCard, { kind: "search" }> }) {
  const answer = (card.answer ?? "").trim();
  const empty = answer === "" && card.sources.length === 0;
  const queries = card.queries && card.queries.length > 1 ? card.queries : null;
  const terms = useMemo(
    () => queryTerms([card.query, ...(card.queries ?? [])].filter(Boolean).join(" ")),
    [card.query, card.queries],
  );
  const provider = providerLabel(card.provider);
  const fellBack = Boolean(card.fallback_from);
  const notes = [
    card.filtered_out ? `${card.filtered_out} removed by the domain or date filters` : "",
    card.failed_queries ? `${card.failed_queries} quer${card.failed_queries === 1 ? "y" : "ies"} failed` : "",
    card.truncated ? "source list was truncated" : "",
  ].filter(Boolean);
  return (
    <div className="rv-card web-search" data-web="search">
      <div className="web-search-head">
        {provider && (
          <span
            className="web-provider"
            data-fallback={fellBack || undefined}
            data-testid="web-provider"
            title={
              fellBack
                ? `${providerLabel(card.fallback_from)} did not answer${card.fallback_reason ? `: ${card.fallback_reason}` : ""}`
                : `Results from ${provider}`
            }
          >
            {provider}
          </span>
        )}
        {fellBack && (
          <span className="web-fallback" data-testid="web-fallback">
            {providerLabel(card.fallback_from)} unavailable
          </span>
        )}
        {queries ? (
          <span className="web-queries" data-testid="web-queries">
            {queries.map((query, index) => (
              <span key={index} className="web-query" title={query}>
                {query}
              </span>
            ))}
          </span>
        ) : (
          <span className="web-search-count">
            {card.sources.length} result{card.sources.length === 1 ? "" : "s"}
          </span>
        )}
        <span className="rv-banner-spacer" />
        <CopyButton text={copyText(card)} />
      </div>
      {empty ? (
        <div className="rv-empty">No results found</div>
      ) : (
        <>
          {answer && (
            // The provider's own prose, rendered as markdown: it is the one part of a
            // search result that is written rather than extracted.
            <div className="web-answer">
              <Markdown text={answer} />
            </div>
          )}
          <ScrollFade className="web-results-scroll">
            <ol className="web-results" data-testid="web-sources">
              {card.sources.map((source, index) => (
                <ResultRow key={`${source.url}-${index}`} source={source} index={index} terms={terms} />
              ))}
            </ol>
          </ScrollFade>
        </>
      )}
      {notes.length > 0 && (
        <div className="web-search-notes" data-testid="web-notes">
          {notes.join(" · ")}
        </div>
      )}
    </div>
  );
}

function copyText(card: Extract<WebResultCard, { kind: "search" }>): string {
  const lines =
    (card.answer ?? "").trim() !== "" ? [card.answer!.trim(), ""] : ([] as string[]);
  card.sources.forEach((source, index) => {
    lines.push(`${index + 1}. ${linkLabel(source)} — ${source.url}`);
    const by = [source.author, source.published_at?.slice(0, 10)].filter(Boolean).join(", ");
    if (by) lines.push(`   ${by}`);
    if (source.snippet) lines.push(`   ${source.snippet}`);
  });
  return lines.join("\n");
}
