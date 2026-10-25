// The modal layer of a session: the command-trust prompt and the `/open` file picker. They
// cover the session, so they live in one file — "what can appear over this conversation" is
// a single question. (Search is not here: the nav
// rail keeps the sidebar's own command palette reachable in every nav state.)

import { OpenFileDialog } from "../OpenFileDialog";
import { WorkspaceTrustPrompt } from "../WorkspaceTrustPrompt";

export interface SessionOverlaysProps {
  workspaceTrustRequest: any;
  setWorkspaceTrustRequest: any;
  // `/open` (the sibling of message revert): the file picker's visibility, the session it
  // searches, and the viewer opener (the RightRail's artifact event).
  filePickerOpen: any;
  filePickerSessionId: any;
  openFileInViewer: any;
  setFilePickerOpen: any;
}

export function SessionOverlays(props: SessionOverlaysProps) {
  const { workspaceTrustRequest, setWorkspaceTrustRequest, filePickerOpen, filePickerSessionId, openFileInViewer, setFilePickerOpen } = props;
  return (
    <>
{workspaceTrustRequest && (
  <WorkspaceTrustPrompt
    request={workspaceTrustRequest}
    onClose={() => setWorkspaceTrustRequest(null)}
  />
)}
{/* `/open`: search this session's folders and open the pick in the RightRail viewer
    (the artifact event the transcript's chips already use). */}
{filePickerOpen && filePickerSessionId && (
  <OpenFileDialog
    sessionId={filePickerSessionId}
    onOpen={openFileInViewer}
    onClose={() => setFilePickerOpen(false)}
  />
)}
    </>
  );
}
