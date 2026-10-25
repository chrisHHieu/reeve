// One fetched page (`web_fetch`), as a reader should get it.
//
// The card used to name the page by its address twice, print the first 800 characters of the
// model's text as one run of raw Markdown (`# Coroutines and tasks¶ … [Lib/asyncio/coroutines.py]
// (<https://…>)`), colour a 404 the green of a 200, and show nothing at all of an answer's quotes
// or a phrase's matches — measured on real pages, 2026-10-05. Now:
//
//   · the head is the page: its own title (which opens it), the address as a trail, the status
//     in the tone of what it means (green answered, red refused), and Copy;
//   · the body is what the call got, by how it read the page: the start of the page rendered
//     as Markdown (cleaned on the server: no images, which would load from a third party past
//     the egress gate, and no link targets); or the question, the answer and each quote marked
//     found or not in the page; or each place the phrase occurs with its position marked;
//   · the foot says how much of the page that was (a bar: the share read and where it sits)
//     and how it was read (main content only, this session's copy, plain http, …).

import type { ReactNode } from "react";
import type { WebFetchCard } from "./types";
import { CopyButton } from "../components/primitives/CopyButton";
import { ScrollFade } from "../components/primitives/ScrollFade";
import { Markdown } from "../components/Markdown";
import { Icon } from "../components/Icon";
import { SourceFav, safeHref } from "./webMarks";
import { hostOf } from "./webSearchView";
import {
  aboutNotes,
  addressTrail,
  compactCount,
  coverage,
  fetchCopyText,
  markPhrase,
  quoteTally,
  statusTone,
} from "./webFetchView";

export function FetchCard({ card }: { card: WebFetchCard }) {
  const href = safeHref(card.url);
  const mode = card.mode ?? "page";
  const tone = statusTone(card.status_code);
  const trail = addressTrail(card.url);
  const title = card.title || hostOf(card.url) || card.url;
  return (
    <div className="rv-card web-fetch" data-web="fetch" data-mode={mode}>
      <div className="web-fetch-head">
        <SourceFav url={card.url} />
        <div className="web-fetch-id">
          {href ? (
            <a className="web-fetch-title" href={href} target="_blank" rel="noreferrer" title={card.title || card.url}>
              {title}
            </a>
          ) : (
            <span className="web-fetch-title" title={card.url}>
              {title}
            </span>
          )}
          <span className="web-fetch-trail" title={card.url} data-testid="web-fetch-meta">
            <span className="web-fetch-host">{trail.host}</span>
            {trail.path && <span className="web-fetch-path">{trail.path}</span>}
          </span>
        </div>
        {card.status_code !== undefined && (
          <span className="web-status" data-tone={tone ?? undefined} data-testid="web-fetch-status">
            {card.status_code}
          </span>
        )}
        <CopyButton text={fetchCopyText(card)} />
      </div>
      <FetchBody card={card} />
      <FetchFoot card={card} />
    </div>
  );
}

function FetchBody({ card }: { card: WebFetchCard }) {
  const mode = card.mode ?? "page";
  if (mode === "answer" && card.answer) return <AnswerBody answer={card.answer} />;
  if (mode === "find" && card.found) return <FindBody found={card.found} pageChars={card.read?.page_chars} />;
  if (mode === "redirect") {
    const to = card.redirect?.url ? safeHref(card.redirect.url) : undefined;
    return (
      <Note>
        Moved to{" "}
        {to ? (
          <a href={to} target="_blank" rel="noreferrer" className="web-fetch-strong">
            {card.redirect?.host || to}
          </a>
        ) : (
          <span className="web-fetch-strong">{card.redirect?.host || "another site"}</span>
        )}{" "}
        — not followed. Fetching that address asks for its own approval.
      </Note>
    );
  }
  if (mode === "image") return <Note>An image, attached for the model to look at.</Note>;
  if (mode === "pdf") return <Note>A PDF, attached for the model to read.</Note>;
  const excerpt = (card.excerpt ?? "").trim();
  if (!excerpt) return <Note>The page held no text.</Note>;
  return (
    <ScrollFade className="web-fetch-scroll" data-testid="web-fetch-excerpt" data-kind={card.excerpt_kind ?? "text"}>
      {card.excerpt_kind === "markdown" ? (
        <div className="web-fetch-page">
          <Markdown text={excerpt} />
        </div>
      ) : (
        <pre className={card.excerpt_kind === "code" ? "web-fetch-code" : "web-fetch-text"}>{excerpt}</pre>
      )}
    </ScrollFade>
  );
}

function Note({ children }: { children: ReactNode }) {
  return <div className="web-fetch-note">{children}</div>;
}

function AnswerBody({ answer }: { answer: NonNullable<WebFetchCard["answer"]> }) {
  const { found, total } = quoteTally(answer);
  return (
    <div className="web-fetch-answer" data-testid="web-fetch-answer">
      {answer.question && (
        <div className="web-fetch-question">
          <span className="web-fetch-label">Asked</span>
          <span>{answer.question}</span>
        </div>
      )}
      <ScrollFade className="web-fetch-scroll">
        <div className="web-fetch-page">
          <Markdown text={answer.text} />
        </div>
      </ScrollFade>
      {answer.quotes.length > 0 ? (
        <div className="web-fetch-quotes" data-testid="web-fetch-quotes">
          <div className="web-fetch-label" data-all={found === total || undefined}>
            Quotes checked against the page · {found} of {total} found
          </div>
          <ul>
            {answer.quotes.map((quote, index) => (
              <li key={index} data-found={quote.found || undefined}>
                <span className="web-fetch-quote-mark" title={quote.found ? "Found in the page" : "Not in the page"}>
                  <Icon name={quote.found ? "check" : "x"} size={12} />
                </span>
                <q>{quote.quote}</q>
                {!quote.found && <span className="web-fetch-quote-miss">not in the page</span>}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="web-fetch-quotes">
          <div className="web-fetch-label" data-none>
            The answer gave no quotes to check
          </div>
        </div>
      )}
    </div>
  );
}

function FindBody({ found, pageChars }: { found: NonNullable<WebFetchCard["found"]>; pageChars?: number }) {
  const shown = found.matches.length;
  const head =
    found.count === 0
      ? `No match for “${found.phrase}”${pageChars ? ` in ${compactCount(pageChars)} characters` : ""}`
      : `${found.count} match${found.count === 1 ? "" : "es"} for “${found.phrase}”${found.count > shown ? ` · first ${shown} shown` : ""}`;
  return (
    <div className="web-fetch-find" data-testid="web-fetch-find">
      <div className="web-fetch-label">{head}</div>
      {shown > 0 && (
        <ScrollFade className="web-fetch-scroll">
          <ol className="web-fetch-matches">
            {found.matches.map((match, index) => (
              <li key={index}>
                <span className="web-fetch-at" title={`At character ${match.at.toLocaleString()} of the page`}>
                  {compactCount(match.at)}
                </span>
                <span className="web-fetch-match">
                  {markPhrase(match.text, found.phrase).map((run, i) =>
                    run.hit ? (
                      <mark key={i} className="web-hit">
                        {run.text}
                      </mark>
                    ) : (
                      run.text
                    ),
                  )}
                </span>
              </li>
            ))}
          </ol>
        </ScrollFade>
      )}
    </div>
  );
}

function FetchFoot({ card }: { card: WebFetchCard }) {
  const read = coverage(card);
  const notes = aboutNotes(card);
  const answer = card.answer;
  const by =
    card.mode === "answer" && answer
      ? [
          answer.by ? `Answered by ${answer.by}` : "Answered",
          card.read?.page_chars ? `from ${compactCount(card.read.page_chars)} characters` : "",
          answer.parts && answer.parts > 1 ? `read in ${answer.parts} parts` : "",
          answer.unread_chars ? `${compactCount(answer.unread_chars)} not read` : "",
        ]
          .filter(Boolean)
          .join(" · ")
      : "";
  if (!read && !notes.length && !by) return null;
  return (
    <div className="web-fetch-foot" data-testid="web-fetch-foot">
      {read && (
        <span className="web-fetch-read" data-whole={read.whole || undefined}>
          {read.from !== undefined && read.to !== undefined && (
            <span className="web-fetch-bar" aria-hidden>
              <span style={{ left: `${read.from * 100}%`, width: `${Math.max(2, (read.to - read.from) * 100)}%` }} />
            </span>
          )}
          {read.text}
        </span>
      )}
      {by && <span>{by}</span>}
      {notes.map((note, index) => (
        <span key={index} data-tone={note.tone}>
          {note.text}
        </span>
      ))}
    </div>
  );
}
