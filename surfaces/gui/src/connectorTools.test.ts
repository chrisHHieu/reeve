import { describe, expect, it } from "vitest";
import { buildConnectorToolIndex } from "./connectorTools";
import { serverIdentity, mcpToolBreakdown } from "./toolMeta";

const CONNECTORS = [
  {
    name: "gmail",
    title: "Gmail",
    logo: "gmail",
    brand_color: "#ea4335",
    tools: [
      { name: "gmail_get_message", label: "Read message" },
      { name: "gmail_search_messages", label: "Search Gmail" },
    ],
  },
  {
    name: "github",
    title: "GitHub",
    logo: "github",
    brand_color: "#1f2328",
    tools: [{ name: "github_create_issue", label: "Create issue" }],
  },
] as any;

describe("buildConnectorToolIndex", () => {
  it("maps every connector tool to its connector, logo, and label", () => {
    const index = buildConnectorToolIndex(CONNECTORS);
    expect(index.get("gmail_get_message")).toEqual({
      connector: "gmail",
      title: "Gmail",
      logo: "gmail",
      brandColor: "#ea4335",
      label: "Read message",
    });
    expect(index.get("github_create_issue")?.title).toBe("GitHub");
    expect(index.size).toBe(3);
  });

  it("is empty for no connectors and ignores tools without a name", () => {
    expect(buildConnectorToolIndex([]).size).toBe(0);
    expect(buildConnectorToolIndex([{ name: "x", tools: [{}] }] as any).size).toBe(0);
  });
});

describe("serverIdentity", () => {
  const index = buildConnectorToolIndex(CONNECTORS);

  it("resolves a connector tool from the index", () => {
    const id = serverIdentity("gmail_get_message", index)!;
    expect(id.key).toBe("connector:gmail");
    expect(id.serverTitle).toBe("Gmail");
    expect(id.logo).toBe("gmail");
    expect(id.toolLabel).toBe("Read message");
    expect(id.isMcp).toBe(false);
  });

  it("resolves an MCP name with no index, and picks up a title when indexed", () => {
    const bare = serverIdentity("mcp__wealthlog__get_portfolio")!;
    expect(bare.isMcp).toBe(true);
    expect(bare.serverTitle).toBe("Wealthlog");
    const titled = serverIdentity("mcp__gmail__get_message", index)!;
    expect(titled.isMcp).toBe(true);
    expect(titled.serverTitle).toBe("Gmail");
  });

  it("returns null for a tool that belongs to no server", () => {
    expect(serverIdentity("read_file", index)).toBeNull();
    expect(serverIdentity("web_search")).toBeNull();
  });
});

describe("mcpToolBreakdown", () => {
  it("uses the connector's labels when the index knows the tool", () => {
    const index = buildConnectorToolIndex(CONNECTORS);
    expect(
      mcpToolBreakdown(["gmail_get_message", "gmail_get_message", "gmail_search_messages"], index),
    ).toBe("Read message ×2, Search Gmail");
  });

  it("falls back to the wire tool name without an index", () => {
    expect(mcpToolBreakdown(["mcp__wealthlog__get_goals", "mcp__wealthlog__get_goals"])).toBe(
      "get_goals ×2",
    );
  });
});

describe("a plugin's MCP server", () => {
  it("names the server and the plugin it came with", async () => {
    const { serverIdentity, pluginServer } = await import("./toolMeta");
    expect(pluginServer("plugin_release-helper_deploy_api")).toEqual({ plugin: "release-helper", name: "deploy_api" });
    expect(pluginServer("jira")).toBeNull();
    const identity = serverIdentity("mcp__plugin_release-helper_deploy_api__create_tag");
    expect(identity?.serverTitle).toBe("Deploy Api (plugin release-helper)");
    expect(identity?.plugin).toBe("release-helper");
    expect(serverIdentity("mcp__jira__create_issue")?.serverTitle).toBe("Jira");
  });
});
