import { useCallback, useLayoutEffect, useRef } from "react";

/**
 * A function whose identity never changes and which always runs the newest `fn`.
 *
 * For a callback that an inline arrow keeps re-creating (`() => fork(sessionId)`) on its way to a
 * `memo`'d child that compares it: the child sees a new function every render and re-renders
 * for nothing. Here that child was every turn of the transcript, on every streamed token.
 * `undefined` stays `undefined`, so an affordance that is absent is still absent.
 */
export function useStableCallback<A extends unknown[], R>(
  fn: ((...args: A) => R) | undefined,
): ((...args: A) => R) | undefined {
  const latest = useRef(fn);
  useLayoutEffect(() => {
    latest.current = fn;
  });
  const stable = useCallback((...args: A) => (latest.current as (...args: A) => R)(...args), []);
  return fn ? stable : undefined;
}
