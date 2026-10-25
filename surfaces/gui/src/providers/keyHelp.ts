// Where each provider hands out API keys, for the "No key yet?" line under the key field: the page
// to open, and how to name it in that sentence.

export const KEY_HELP: Record<string, { url: string; label: string }> = {
  anthropic: { url: "https://console.anthropic.com/settings/keys", label: "console.anthropic.com" },
  openai: { url: "https://platform.openai.com/api-keys", label: "platform.openai.com" },
  gemini: { url: "https://aistudio.google.com/apikey", label: "aistudio.google.com" },
  ark: { url: "https://console.byteplus.com/ark/region:ark+ap-southeast-1/apiKey", label: "console.byteplus.com" },
  "ark-agent-plan-cn": {
    url: "https://console.volcengine.com/ark/region:cn-beijing/openManagement?LLM=%7B%7D&advancedActiveKey=agentPlan",
    label: "console.volcengine.com",
  },
  openrouter: { url: "https://openrouter.ai/keys", label: "openrouter.ai" },
  "opencode-go": { url: "https://opencode.ai/auth", label: "opencode.ai" },
  bedrock: { url: "https://console.aws.amazon.com/bedrock/home#/api-keys", label: "the AWS Bedrock console" },
  fireworks: { url: "https://fireworks.ai/account/api-keys", label: "fireworks.ai" },
  together: { url: "https://api.together.xyz/settings/api-keys", label: "together.xyz" },
  zai: { url: "https://z.ai/manage-apikey/apikey-list", label: "z.ai" },
  "zhipuai-coding-plan": { url: "https://open.bigmodel.cn/usercenter/apikeys", label: "open.bigmodel.cn" },
  "zai-coding-plan": { url: "https://z.ai/manage-apikey/apikey-list", label: "z.ai" },
  kimi: { url: "https://platform.moonshot.ai/console/api-keys", label: "platform.moonshot.ai" },
  deepseek: { url: "https://platform.deepseek.com/api_keys", label: "platform.deepseek.com" },
  mistral: { url: "https://console.mistral.ai/api-keys", label: "console.mistral.ai" },
  qwen: { url: "https://modelstudio.console.alibabacloud.com", label: "alibabacloud.com" },
  minimax: { url: "https://platform.minimax.io", label: "platform.minimax.io" },
  xai: { url: "https://console.x.ai", label: "console.x.ai" },
};
