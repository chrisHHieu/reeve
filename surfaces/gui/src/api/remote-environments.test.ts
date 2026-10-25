// The remote readout is `user@host:path` and the path half is a
// POSIX path ON THE HOST. A local spelling (Windows drive, relative fragment) is a fact
// about the wrong machine and must never be appended; the destination alone is honest.
import { expect, it } from "vitest";
import {
  formatRemoteEnvironmentDestination,
  formatRemoteEnvironmentReadout,
  type RemoteEnvironmentSummary,
} from "./remote-environments";

const HOST: RemoteEnvironmentSummary = {
  id: "h1",
  name: "Build server",
  user: "ubuntu",
  host: "192.168.2.125",
  port: 1122,
};

it("shows the destination and the host's absolute path when both are known", () => {
  expect(formatRemoteEnvironmentReadout(HOST, "/home/ubuntu")).toBe(
    "ubuntu@192.168.2.125:1122:/home/ubuntu",
  );
});

it("falls back to the bare destination when the remote path is unknown", () => {
  expect(formatRemoteEnvironmentReadout(HOST, null)).toBe("ubuntu@192.168.2.125:1122");
  expect(formatRemoteEnvironmentReadout(HOST, "")).toBe("ubuntu@192.168.2.125:1122");
  expect(formatRemoteEnvironmentReadout(HOST, undefined)).toBe("ubuntu@192.168.2.125:1122");
});

it("never appends a local path spelling — not even the owner's screenshot shape", () => {
  // The bug's exact string: a stale local workspace passed as the remote path.
  expect(formatRemoteEnvironmentReadout(HOST, "C:\\Users\\DELI")).toBe(
    "ubuntu@192.168.2.125:1122",
  );
  expect(formatRemoteEnvironmentReadout(HOST, "C:/Users/DELI")).toBe(
    "ubuntu@192.168.2.125:1122",
  );
  expect(formatRemoteEnvironmentReadout(HOST, "home/ubuntu")).toBe(
    "ubuntu@192.168.2.125:1122",
  );
});

it("returns nothing without a bound environment", () => {
  expect(formatRemoteEnvironmentReadout(null, "/home/ubuntu")).toBe("");
});

it("shows the port only when it is not the SSH default", () => {
  expect(formatRemoteEnvironmentDestination(HOST)).toBe("ubuntu@192.168.2.125:1122");
  expect(formatRemoteEnvironmentDestination({ ...HOST, port: 22 })).toBe(
    "ubuntu@192.168.2.125",
  );
});
