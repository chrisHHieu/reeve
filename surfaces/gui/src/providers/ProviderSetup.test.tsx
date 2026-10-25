// A provider that offers ways to sign in (Bedrock's "Connect with"): only the chosen way's fields
// are drawn (`show_when`), and choosing another way changes them.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { KEY_HELP, ProviderForm, ProviderMark, type ProviderSetupState } from "./ProviderSetup";
import type { ProviderInfo } from "../api";

vi.mock("../desktop", () => ({ openExternal: vi.fn() }));
vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  providerSignin: vi.fn(async () => ({ ok: true })),
  providerAuthStatus: vi.fn(async () => ({ signed_in: false, authorizing: false })),
  providerSignout: vi.fn(async () => ({ ok: true })),
  removeProvider: vi.fn(async () => ({ ok: true })),
}));

afterEach(cleanup);

const field = (key: string, label: string, more: Partial<ProviderInfo["fields"][number]> = {}) => ({ key, label, secret: false, required: false, help: "", placeholder: "", ...more });

const BEDROCK: ProviderInfo = {
  name: "bedrock",
  title: "AWS Bedrock",
  needs_key: true,
  configured: false,
  values: {},
  suggested_models: [],
  fields: [
    field("region", "AWS region", { required: true, placeholder: "us-east-1" }),
    field("auth_method", "Connect with", {
      default: "api_key",
      choices: [
        { value: "api_key", label: "Bedrock API key" },
        { value: "profile", label: "AWS profile" },
        { value: "iam", label: "IAM keys" },
      ],
    }),
    field("bedrock_api_key", "Bedrock API key", { secret: true, placeholder: "ABSK…", show_when: { auth_method: "api_key" } }),
    field("aws_profile", "AWS profile", { placeholder: "default", show_when: { auth_method: "profile" } }),
    field("aws_secret_access_key", "Secret access key", { secret: true, show_when: { auth_method: "iam" } }),
  ],
};

function setup(fields: Record<string, string>, setFieldValue = vi.fn()): ProviderSetupState {
  return {
    providers: [BEDROCK],
    ordered: [BEDROCK],
    refreshProviders: async () => {},
    sel: "bedrock",
    info: BEDROCK,
    fields,
    setFieldValue,
    dirty: false,
    verify: { state: "idle" },
    showEndpoint: false,
    setShowEndpoint: () => {},
    keylessOk: new Set(),
    credentialed: false,
    savedState: false,
    secretFilled: true,
    openProvider: () => {},
    backToGallery: () => {},
    runTestAndSave: async () => true,
    removeKey: async () => {},
    cancelBackTimer: () => {},
    statusFor: () => null,
    saveField: async () => {},
    fieldSaved: null,
  };
}

const shown = (key: string) => screen.queryByTestId(`t-field-${key}`) !== null;

describe("ProviderForm's way to sign in", () => {
  it("draws only the fields of the way chosen", () => {
    render(<ProviderForm ps={setup({ auth_method: "api_key" })} tp="t" />);
    expect([shown("bedrock_api_key"), shown("aws_profile"), shown("aws_secret_access_key")]).toEqual([true, false, false]);
    expect(screen.getByTestId("t-choice-auth_method-api_key").getAttribute("aria-checked")).toBe("true");
  });

  it("choosing another way changes the fields", () => {
    const setFieldValue = vi.fn();
    const { rerender } = render(<ProviderForm ps={setup({ auth_method: "api_key" }, setFieldValue)} tp="t" />);
    fireEvent.click(screen.getByTestId("t-choice-auth_method-profile"));
    expect(setFieldValue).toHaveBeenCalledWith("auth_method", "profile");
    rerender(<ProviderForm ps={setup({ auth_method: "profile" }, setFieldValue)} tp="t" />);
    expect([shown("aws_profile"), shown("bedrock_api_key")]).toEqual([true, false]);
  });

  it("IAM keys shows the key pair", () => {
    render(<ProviderForm ps={setup({ auth_method: "iam" })} tp="t" />);
    expect([shown("aws_secret_access_key"), shown("bedrock_api_key")]).toEqual([true, false]);
  });
});

describe("the two Ark providers", () => {
  it("each has its own mark, BytePlus's and Volcengine's", () => {
    const { container, rerender } = render(<ProviderMark name="ark" title="BytePlus Ark" />);
    expect(container.querySelector("img")).toBeTruthy();
    rerender(<ProviderMark name="ark-agent-plan-cn" title="Volcengine Ark Agent Plan" />);
    expect(container.querySelector("img")).toBeTruthy();
  });

  it("each sends the person to its own console for a key", () => {
    expect(KEY_HELP.ark.url).toBe("https://console.byteplus.com/ark/region:ark+ap-southeast-1/apiKey");
    expect(KEY_HELP["ark-agent-plan-cn"].url).toContain("advancedActiveKey=agentPlan");
  });
});
