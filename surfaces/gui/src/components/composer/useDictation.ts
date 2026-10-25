import { useEffect, useState } from "react";
import { cancelDictation, getDictationLevel, getDictationStatus, isDesktop, startDictation, stopDictation, type DictationStatus } from "../../desktop";
import { VOICE_INPUT_EVENT } from "../settings/VoiceInputSettings";

// The composer's microphone. The desktop app's only: a browser build stays a client of the
// local server and never turns on the browser's microphone or sends audio anywhere.

/** How many loudness samples the wave keeps; it scrolls left as new ones come. */
export const WAVE_BARS = 14;

type Options = {
  /** Words the model heard, to add to the draft. */
  onWords: (words: string) => void;
  /** Voice input is not set up: where to send the person instead of recording. */
  onConfigure?: () => void;
  /** After the words land, the box takes the focus back. */
  focus?: () => void;
};

export function useDictation({ onWords, onConfigure, focus }: Options) {
  const [dictation, setDictation] = useState<DictationStatus | null>(null);
  // What the microphone is doing between a press and its answer ("Transcribing…").
  const [dictationBusy, setDictationBusy] = useState<string | null>(null);
  const [dictationError, setDictationError] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [levels, setLevels] = useState<number[]>([]);
  const recording = !!dictation?.recording;

  const reread = () => getDictationStatus().then((status) => status && setDictation(status));

  // The state, and every change Settings ▸ Voice input announces (it sends the new state along).
  useEffect(() => {
    if (!isDesktop()) return;
    const follow = (event?: Event) => {
      const told = (event as CustomEvent<DictationStatus> | undefined)?.detail;
      if (told) setDictation(told);
      else void reread();
    };
    follow();
    window.addEventListener(VOICE_INPUT_EVENT, follow);
    return () => window.removeEventListener(VOICE_INPUT_EVENT, follow);
  }, []);

  // While recording: the time, and the microphone's loudness about ten times a second, so the wave
  // is a real meter (owner: the first wave's bars were fixed and read as fake). Escape
  // throws the recording away.
  useEffect(() => {
    if (!recording) {
      setSeconds(0);
      setLevels([]);
      return;
    }
    const started = Date.now();
    const clock = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 250);
    const meter = window.setInterval(() => {
      void getDictationLevel().then((level) => typeof level === "number" && setLevels((cur) => [...cur.slice(-(WAVE_BARS - 1)), level]));
    }, 100);
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      void cancelDictation()
        .catch(() => undefined)
        .finally(() => void reread());
    };
    window.addEventListener("keydown", escape);
    return () => {
      window.clearInterval(clock);
      window.clearInterval(meter);
      window.removeEventListener("keydown", escape);
    };
  }, [recording]);

  const voiceReady = !!dictation?.supported && !!dictation?.model_verified && !!dictation?.test_passed;
  const recordingTime = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

  // A press stops a recording and adds its words to the draft, or starts one, or, with voice input
  // not set up, opens its settings. A failure says why and reads the state again, so the microphone
  // is never left stuck.
  const toggleDictation = async () => {
    if (!isDesktop() || dictationBusy) return;
    setDictationError(null);
    try {
      if (recording) {
        setDictationBusy("Transcribing…");
        const words = await stopDictation();
        if (words === null) throw new Error("Could not transcribe your recording.");
        if (words.trim()) onWords(words.trim());
        setDictation(await getDictationStatus());
        focus?.();
        return;
      }
      const status = dictation || (await getDictationStatus());
      if (!status) throw new Error("Voice dictation is unavailable.");
      if (!status.supported || !status.model_verified || !status.test_passed) return void onConfigure?.();
      setDictationBusy("Starting microphone…");
      const started = await startDictation();
      if (!started?.recording) throw new Error("Could not start the microphone.");
      setDictation(started);
    } catch (error) {
      setDictationError(error instanceof Error ? error.message : "Voice dictation is unavailable.");
      await reread();
    } finally {
      setDictationBusy(null);
    }
  };

  return { dictation, dictationBusy, dictationError, levels, recordingTime, voiceReady, toggleDictation };
}
