import { useEffect, useState } from "react";
import {
  createRemoteEnvironment,
  deleteRemoteEnvironment,
  draftFromProfile,
  emptyRemoteEnvironmentDraft,
  formatRemoteEnvironmentDestination,
  listRemoteEnvironments,
  testRemoteEnvironment,
  updateRemoteEnvironment,
  validateRemoteEnvironmentDraft,
  type RemoteEnvironmentDraft,
  type RemoteEnvironmentProfile,
  type RemoteEnvironmentTestResult,
} from "../api";
import { Icon } from "./Icon";
import { SETTINGS_INPUT, SettingsCard, SettingsPageAction, SettingsRow, TextButton } from "./settings/primitives";

// Settings ▸ Remote (phases 1–2b + password auth): SSH profiles + the real Test
// Connection.
//
// Honest boundaries, rendered: Save stores a destination on this machine; a password is
// never part of the profile — unchecked it is used for the connection only, checked it
// goes to Windows Credential Manager and the profile keeps a `remembered` flag. Test
// Connection runs the system ssh server-side and reports platform/arch/home or an
// actionable refusal, and nothing here starts a session — the welcome chip's pick does.

// The host form speaks Settings' own controls (2026-10-10 redesign): its inputs and buttons are the
// ones every other page uses, and it sits in a card of its own under the list.
const FIELD_HELP = "text-[12px] text-muted mt-1.5 leading-relaxed";
const INPUT = SETTINGS_INPUT;
const BTN_ACCENT = "settings-text-btn accent";
const BTN_BORDERED = "settings-text-btn";

type TestState =
  | { state: "untested" }
  | { state: "testing" }
  | { state: "ok"; result: RemoteEnvironmentTestResult }
  | { state: "failed"; result: RemoteEnvironmentTestResult };

function statusLabel(status: TestState | undefined): string {
  if (status?.state === "ok") return "Connected";
  if (status?.state === "failed") return "Disconnected";
  return "Not tested";
}

function statusTone(status: TestState | undefined): string {
  if (status?.state === "ok") return "text-ok";
  if (status?.state === "failed") return "text-red-600";
  return "text-muted";
}

export function RemoteEnvironmentsSection() {
  const [profiles, setProfiles] = useState<RemoteEnvironmentProfile[] | null>(null);
  const [draft, setDraft] = useState<RemoteEnvironmentDraft | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tests, setTests] = useState<Record<string, TestState>>({});
  // The form's own probe: "did what I typed actually connect?" — never saved, never logged.
  const [formTest, setFormTest] = useState<TestState | null>(null);
  const [showPassword, setShowPassword] = useState(false);

  const refresh = async () => {
    try {
      setProfiles(await listRemoteEnvironments());
      setListError(null);
    } catch {
      setProfiles([]);
      setListError("Could not load SSH hosts from the local server.");
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const openNew = () => {
    setDraft(emptyRemoteEnvironmentDraft());
    setEditingId(null);
    setFormError(null);
    setFormTest(null);
    setShowPassword(false);
  };

  const openEdit = (profile: RemoteEnvironmentProfile) => {
    setDraft(draftFromProfile(profile));
    setEditingId(profile.id);
    setFormError(null);
    setFormTest(null);
    setShowPassword(false);
  };

  const closeForm = () => {
    setDraft(null);
    setEditingId(null);
    setFormError(null);
    setFormTest(null);
    setShowPassword(false);
  };

  const save = async () => {
    if (!draft || busy) return;
    const invalid = validateRemoteEnvironmentDraft(draft);
    if (invalid) {
      setFormError(invalid);
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      const result = editingId
        ? await updateRemoteEnvironment(editingId, draft)
        : await createRemoteEnvironment(draft);
      if (!result.ok || !result.environment) {
        setFormError(result.error || "Could not save the SSH host.");
        return;
      }
      closeForm();
      await refresh();
    } catch {
      setFormError("Could not reach the local server. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (profile: RemoteEnvironmentProfile) => {
    if (!window.confirm(`Delete “${profile.name}”? The remote host itself is untouched.`)) return;
    try {
      await deleteRemoteEnvironment(profile.id);
      setTests((current) => {
        const next = { ...current };
        delete next[profile.id];
        return next;
      });
      await refresh();
    } catch {
      setListError("Could not delete the SSH host. Try again.");
    }
  };

  const runTest = async (profile: RemoteEnvironmentProfile) => {
    setTests((current) => ({ ...current, [profile.id]: { state: "testing" } }));
    try {
      const result = await testRemoteEnvironment(profile.id);
      setTests((current) => ({
        ...current,
        [profile.id]: result.ok ? { state: "ok", result } : { state: "failed", result },
      }));
    } catch {
      setTests((current) => ({
        ...current,
        [profile.id]: {
          state: "failed",
          result: {
            ok: false,
            platform: null,
            arch: null,
            home: null,
            error: "Could not reach the local server. Try again.",
          },
        },
      }));
    }
  };

  const runFormTest = async () => {
    // Only a SAVED host has an id to test; the entered password rides this call
    // transiently (the server uses it and writes nothing).
    if (!draft || !editingId || busy || formTest?.state === "testing") return;
    setFormTest({ state: "testing" });
    try {
      const result = await testRemoteEnvironment(editingId, draft.password);
      setFormTest(result.ok ? { state: "ok", result } : { state: "failed", result });
    } catch {
      setFormTest({
        state: "failed",
        result: {
          ok: false,
          platform: null,
          arch: null,
          home: null,
          error: "Could not reach the local server. Try again.",
        },
      });
    }
  };

  return (
    <section>
      {/* With no host yet the empty list offers Add host itself: one way to do the one thing. */}
      {profiles && profiles.length > 0 ? (
        <SettingsPageAction>
          <TextButton tone="accent" testId="remote-new" disabled={busy} onClick={openNew}>
            <Icon name="plus" size={13} /> Add host
          </TextButton>
        </SettingsPageAction>
      ) : null}
      {listError && (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-[12px] text-red-700 mb-4">
          {listError}
        </div>
      )}

      <SettingsCard
        title="SSH hosts"
        description="An identity file is a path on this machine. Passwords are never written to the profile file or the session wire; a remembered one lives in Windows Credential Manager."
        testId="remote-environments-card"
        tile
      >
        {profiles === null ? (
          <div className="px-4 py-4 settings-note">Loading SSH hosts…</div>
        ) : profiles.length === 0 ? (
          // With none yet, the page says what a host is for and offers the one thing to do
          // (settings redesign, 2026-10-20).
          <div className="settings-empty" data-testid="remote-empty">
            <Icon name="server" size={20} className="settings-empty-mark" />
            <div className="settings-empty-title">Work on another computer</div>
            <p className="settings-empty-text">
              Add a computer you reach with SSH. Reeve runs commands and edits files there, and the conversation stays on this one.
            </p>
            <button type="button" className="settings-primary-btn" data-testid="remote-empty-add" disabled={busy} onClick={openNew}>
              Add host
            </button>
          </div>
        ) : (
          profiles.map((profile, index) => {
            const status = tests[profile.id];
            return (
              <SettingsRow
                key={`${profile.id}-${index}`}
                testId="remote-row"
                title={profile.name}
                detail={
                  <>
                    <span className="block truncate">
                      {formatRemoteEnvironmentDestination(profile)}
                      {profile.remembered && (
                        <span data-testid="remote-remembered"> · password remembered</span>
                      )}
                    </span>
                    <span className="block" data-testid="remote-status">
                      <span className={statusTone(status)}>Connection: {statusLabel(status)}</span>
                      {status?.state === "ok" && (
                        <span className="text-muted">
                          {" "}
                          · {[status.result.platform, status.result.arch].filter(Boolean).join(" ")}
                          {status.result.home ? ` · ${status.result.home}` : ""}
                        </span>
                      )}
                    </span>
                    {status?.state === "failed" && status.result.error && (
                      <span
                        className="block text-danger mt-1 whitespace-pre-wrap"
                        data-testid="remote-status-error"
                      >
                        {status.result.error}
                      </span>
                    )}
                  </>
                }
              >
                <TextButton testId="remote-edit" disabled={busy} onClick={() => openEdit(profile)}>
                  Edit
                </TextButton>
                <TextButton
                  testId="remote-test"
                  disabled={busy || status?.state === "testing"}
                  onClick={() => void runTest(profile)}
                >
                  {status?.state === "testing" ? "Testing…" : "Test Connection"}
                </TextButton>
                <TextButton tone="danger" testId="remote-delete" disabled={busy} onClick={() => void remove(profile)}>
                  Delete
                </TextButton>
              </SettingsRow>
            );
          })
        )}
      </SettingsCard>

      {draft && (
        <section className="settings-card-block" data-testid="remote-form">
          <h2 className="settings-card-heading">{editingId ? "Edit host" : "New host"}</h2>
          <div className="settings-panel settings-form">
          <div className="settings-form-grid">
            <label className="block">
              <span className="block text-[12px] text-muted mb-1">Name</span>
              <input
                className={INPUT}
                data-testid="remote-name"
                value={draft.name}
                placeholder="Build server"
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </label>
            <label className="block">
              <span className="block text-[12px] text-muted mb-1">SSH host</span>
              <input
                className={INPUT}
                data-testid="remote-host"
                value={draft.host}
                placeholder="dev.example.com or ssh-config-alias"
                spellCheck={false}
                autoComplete="off"
                onChange={(e) => setDraft({ ...draft, host: e.target.value })}
              />
            </label>
            <label className="block">
              <span className="block text-[12px] text-muted mb-1">User (optional)</span>
              <input
                className={INPUT}
                data-testid="remote-user"
                value={draft.user}
                placeholder="ubuntu"
                spellCheck={false}
                autoComplete="off"
                onChange={(e) => setDraft({ ...draft, user: e.target.value })}
              />
            </label>
            <label className="block">
              <span className="block text-[12px] text-muted mb-1">Port</span>
              <input
                className={INPUT}
                data-testid="remote-port"
                type="number"
                min={1}
                max={65535}
                value={draft.port}
                placeholder="22"
                onChange={(e) => setDraft({ ...draft, port: e.target.value })}
              />
            </label>
          </div>
          <label className="block mt-3">
            <span className="block text-[12px] text-muted mb-1">Identity file (optional)</span>
            <input
              className={INPUT}
              data-testid="remote-identity"
              value={draft.identity_file}
              placeholder="~/.ssh/id_ed25519"
              spellCheck={false}
              autoComplete="off"
              onChange={(e) => setDraft({ ...draft, identity_file: e.target.value })}
            />
          </label>

          <div className="mt-3">
            <span className="block text-[12px] text-muted mb-1">Password (optional)</span>
            <div className="flex gap-2">
              <input
                className={INPUT}
                data-testid="remote-password"
                type={showPassword ? "text" : "password"}
                value={draft.password}
                placeholder={
                  draft.stored && editingId
                    ? "Remembered — leave blank to keep it"
                    : ""
                }
                spellCheck={false}
                autoComplete="off"
                onChange={(e) => setDraft({ ...draft, password: e.target.value })}
              />
              <button
                type="button"
                className={BTN_BORDERED}
                data-testid="remote-password-toggle"
                disabled={busy}
                onClick={() => setShowPassword((visible) => !visible)}
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>
            <label className="mt-2 flex items-center gap-2 text-[12px] text-muted">
              <input
                type="checkbox"
                data-testid="remote-remember"
                checked={draft.remember}
                disabled={busy}
                onChange={(e) => setDraft({ ...draft, remember: e.target.checked })}
              />
              Remember password
            </label>
            <div className={FIELD_HELP} data-testid="remote-password-help">
              {draft.remember
                ? "Stored in Windows Credential Manager; ssh still requires the host key to be trusted."
                : "Used for this connection only — not saved. ssh still requires the host key to be trusted."}
            </div>
          </div>

          {formError && (
            <div className="text-[12px] text-red-700 mt-2.5" data-testid="remote-form-error">
              {formError}
            </div>
          )}

          <div className="mt-4 flex items-start justify-between gap-3">
            <div className="min-w-0">
              {editingId ? (
                <button
                  className={BTN_BORDERED}
                  data-testid="remote-form-test"
                  disabled={busy || formTest?.state === "testing"}
                  onClick={() => void runFormTest()}
                >
                  {formTest?.state === "testing" ? "Testing…" : "Test Connection"}
                </button>
              ) : (
                <span className="text-[12px] text-muted" data-testid="remote-form-test-hint">
                  Save the host, then test the connection from its row.
                </span>
              )}
              {formTest && (
                <div className="text-[12px] mt-2" data-testid="remote-form-test-status">
                  <span className={statusTone(formTest)}>
                    Connection: {statusLabel(formTest)}
                  </span>
                  {formTest.state === "ok" && (
                    <span className="text-muted">
                      {" "}
                      · {[formTest.result.platform, formTest.result.arch]
                        .filter(Boolean)
                        .join(" ")}
                      {formTest.result.home ? ` · ${formTest.result.home}` : ""}
                    </span>
                  )}
                  {formTest.state === "failed" && formTest.result.error && (
                    <div
                      className="text-red-700 mt-1 whitespace-pre-wrap"
                      data-testid="remote-form-test-error"
                    >
                      {formTest.result.error}
                    </div>
                  )}
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2 shrink-0">
              <button className={BTN_BORDERED} data-testid="remote-cancel" disabled={busy} onClick={closeForm}>
                Cancel
              </button>
              <button className={BTN_ACCENT} data-testid="remote-save" disabled={busy} onClick={() => void save()}>
                {busy ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
          </div>
        </section>
      )}
    </section>
  );
}
