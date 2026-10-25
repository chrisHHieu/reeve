import { useEffect, useState } from "react";
import {
  cancelDictationModelDownload,
  deleteDictationModel,
  downloadDictationModel,
  getDictationStatus,
  isDesktop,
  listenDictationDownloadProgress,
  markDictationTestPassed,
  startDictation,
  stopDictation,
  verifyDictationModel,
  type DictationDownloadProgress,
  type DictationStatus,
} from "../../desktop";
import { SettingsCard, SettingsRow, TextButton } from "./primitives";

// Settings ▸ Voice input: speaking in the composer, transcribed on this computer by a local
// Whisper model. The microphone shows in the composer once the device can run the model, the
// model is downloaded and verified, and a test recording came back with words.

/** Told to the composer each time the state changes, so its microphone appears or goes. */
export const VOICE_INPUT_EVENT = "reeve:voice-input-changed";

/** The model's size before the device has said it. */
const MODEL_BYTES = 147_964_211;

type Phase = "idle" | "downloading" | "verifying" | "testing" | "transcribing";

const mib = (bytes: number) => (bytes ? `${Math.round(bytes / 1024 / 1024)} MiB` : "0 MiB");
const reason = (error: unknown) => (error instanceof Error ? error.message : typeof error === "string" ? error : "Voice Input could not complete that action.");

/** The dictation state and what can be done to it. Every step that fails says why and reads the
 *  state again, so the page never shows a step as done that was not. */
function useDictation(desktop: boolean) {
  const [status, setStatus] = useState<DictationStatus | null>(null);
  const [progress, setProgress] = useState<DictationDownloadProgress | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [heard, setHeard] = useState("");

  const publish = (next: DictationStatus) => {
    setStatus(next);
    window.dispatchEvent(new CustomEvent(VOICE_INPUT_EVENT, { detail: next }));
  };
  const reread = async () => {
    const latest = await getDictationStatus();
    if (latest) publish(latest);
  };

  // On opening: the state, the download's progress as it comes, and a check of a model that is on
  // disk but not verified yet.
  useEffect(() => {
    if (!desktop) return;
    let live = true;
    let stopListening = () => {};
    void listenDictationDownloadProgress((next) => live && setProgress(next)).then((stop) => {
      stopListening = stop;
    });
    void getDictationStatus().then(async (first) => {
      if (!live || !first) return;
      publish(first);
      if (!first.model_installed || first.model_verified) return;
      setPhase("verifying");
      try {
        const verified = await verifyDictationModel();
        if (live) publish(verified);
      } catch (failure) {
        if (live) setError(reason(failure));
      } finally {
        if (live) setPhase("idle");
      }
    });
    return () => {
      live = false;
      stopListening();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktop]);

  const download = async () => {
    setError(null);
    setProgress({ downloaded_bytes: 0, total_bytes: status?.model_bytes || 0 });
    setPhase("downloading");
    try {
      publish(await downloadDictationModel());
    } catch (failure) {
      setError(reason(failure));
      await reread();
    } finally {
      setPhase("idle");
    }
  };

  const cancel = () => cancelDictationModelDownload().catch(() => undefined);

  // A model that fails its check is deleted and fetched again.
  const repair = async () => {
    setError(null);
    try {
      publish(await deleteDictationModel());
      await download();
    } catch (failure) {
      setError(reason(failure));
    }
  };

  const remove = async () => {
    if (!window.confirm("Delete the local Whisper model and disable Voice Input?")) return;
    setError(null);
    try {
      publish(await deleteDictationModel());
      setHeard("");
      setProgress(null);
    } catch (failure) {
      setError(reason(failure));
    }
  };

  // The test: record, stop, and pass only when the model heard words.
  const test = async () => {
    if (!status?.supported || !status.model_verified) return;
    setError(null);
    try {
      if (status.recording) {
        setPhase("transcribing");
        const words = (await stopDictation()).trim();
        setHeard(words);
        if (!words) throw new Error("No speech was detected. Try again and speak for a little longer.");
        publish(await markDictationTestPassed());
      } else {
        setHeard("");
        setPhase("testing");
        publish(await startDictation());
      }
    } catch (failure) {
      setError(reason(failure));
      await reread();
    } finally {
      setPhase("idle");
    }
  };

  return { status, progress, phase, error, heard, download, cancel, repair, remove, test };
}

const REQUIREMENTS: [string, string][] = [
  ["Mac", "macOS 12+ · Apple Silicon M1+"],
  ["Windows", "Windows 10 22H2/11 · x64"],
  ["Memory", "8 GB recommended"],
  ["Processor", "4 CPU cores recommended"],
];

const OK_TAG = "text-[12px] px-2 py-0.5 rounded-full bg-okSoft text-ok";

export function VoiceInputSettings() {
  const desktop = isDesktop();
  const { status, progress, phase, error, heard, download, cancel, repair, remove, test } = useDictation(desktop);

  if (!desktop) {
    return (
      <SettingsCard title="Voice input">
        <SettingsRow title="Speak in the composer" detail="Recordings and transcripts stay on this computer. Setup is in the Reeve desktop app.">
          <span className="settings-status">Desktop app only</span>
        </SettingsRow>
      </SettingsCard>
    );
  }

  const downloading = phase === "downloading" || !!status?.download_in_progress;
  const total = progress?.total_bytes || status?.model_bytes || 1;
  const percent = Math.min(100, Math.round(((progress?.downloaded_bytes || 0) / total) * 100));
  const ready = !!status?.supported && !!status?.model_verified && !!status?.test_passed;
  const testLabel = status?.recording ? "Stop and check" : phase === "transcribing" ? "Transcribing…" : ready ? "Test again" : "Test microphone";

  let modelAction;
  if (status?.model_verified)
    modelAction = (
      <>
        <span className={OK_TAG}>Verified</span>
        <TextButton onClick={() => void repair()}>Repair</TextButton>
        <TextButton tone="danger" onClick={() => void remove()}>
          Delete
        </TextButton>
      </>
    );
  else if (downloading) modelAction = <TextButton onClick={() => void cancel()}>Cancel</TextButton>;
  else if (phase === "verifying") modelAction = <span className="settings-note">Verifying…</span>;
  else
    modelAction = (
      <TextButton tone="accent" disabled={!status?.supported} onClick={() => void download()}>
        Download model
      </TextButton>
    );

  return (
    <>
      <SettingsCard title="Voice input" tile>
        <SettingsRow title="This device" description="Audio is held in memory only while you record and is transcribed locally." detail={status?.device_summary || "Checking compatibility…"}>
          {status && (
            <span className={"text-[12px] px-2 py-0.5 rounded-full " + (status.supported ? "bg-okSoft text-ok" : "bg-dangerSoft text-danger")}>
              {status.supported ? "● Compatible" : "Unsupported"}
            </span>
          )}
        </SettingsRow>
        {status?.compatibility_reason && <div className="px-4 pb-3 -mt-1 settings-note">{status.compatibility_reason}</div>}
        <div className="grid grid-cols-2 gap-3 px-4 pt-1 pb-4 text-[12px]">
          {REQUIREMENTS.map(([what, needs]) => (
            <div key={what}>
              <span className="block text-ink font-medium">{what}</span>
              <span className="text-muted">{needs}</span>
            </div>
          ))}
        </div>
      </SettingsCard>

      <SettingsCard title="Local model">
        <SettingsRow
          title="Whisper Base · English"
          description="A local voice model. Nothing is uploaded; the download happens once and stays on this machine."
          detail={status?.model_verified ? `Installed and verified · ${mib(status.model_bytes)}` : `Local voice model · ${mib(status?.model_bytes || MODEL_BYTES)}`}
          wide
        >
          <div className="flex items-center gap-2">{modelAction}</div>
          {downloading && (
            <div className="w-full max-w-[420px]">
              <div className="settings-progress">
                <span style={{ width: `${percent}%` }} />
              </div>
              <div className="mt-1.5 settings-note flex">
                <span>
                  {mib(progress?.downloaded_bytes || 0)} of {mib(total)}
                </span>
                <span className="ml-auto">{percent}%</span>
              </div>
            </div>
          )}
        </SettingsRow>
      </SettingsCard>

      <SettingsCard title="Microphone test">
        <SettingsRow
          title="Microphone test"
          description="Record a short phrase to enable the composer microphone."
          detail={ready ? "Your microphone and local transcription engine are working." : undefined}
          wide
        >
          <div className="flex items-center gap-2">
            {ready && <span className={OK_TAG}>● Ready</span>}
            <TextButton disabled={!status?.supported || !status?.model_verified || phase === "transcribing"} onClick={() => void test()}>
              {testLabel}
            </TextButton>
          </div>
          {status?.recording && (
            <div className="text-[12.5px] text-accent" role="status">
              ● Listening… speak a short phrase, then stop.
            </div>
          )}
          {heard && <div className="text-[13px] text-ink">“{heard}”</div>}
        </SettingsRow>
      </SettingsCard>

      {error && (
        <div role="alert" className="settings-alert">
          {error}
        </div>
      )}
    </>
  );
}
