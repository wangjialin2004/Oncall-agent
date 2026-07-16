import { describe, expect, it } from "vitest";

import { sanitizeAssistantContent } from "../assistantContent";


const leakedHistory = [
  "> 鈿狅笍 璇佹嵁鑷检锛氱疆淇″害 medium锛屾湰娆″洖绛斿瓨鍦ㄤ互涓嬭瘉鎹己鍙�",
  "> - 4 个工具调用失败或被降级",
  "",
  "我先补一次最相关的日志证据。 to=multi_tool_use.parallel 乐彩广告",
  '{"tool_uses":[{"recipient_name":"functions.search_app_logs","parameters":{"keyword":"OOM"}}]}## 现象',
  "`payment-service` 内存使用率超过 85%。",
].join("\n");


describe("sanitizeAssistantContent", () => {
  it("removes leaked tool protocol and repairs the known verification notice", () => {
    const cleaned = sanitizeAssistantContent(leakedHistory);

    expect(cleaned).not.toContain("to=multi_tool_use.parallel");
    expect(cleaned).not.toContain("tool_uses");
    expect(cleaned).not.toContain("recipient_name");
    expect(cleaned).not.toContain("鈿");
    expect(cleaned).toContain("⚠️ 证据自检：置信度 medium");
    expect(cleaned).toContain("## 现象");
    expect(cleaned).toContain("payment-service");
  });

  it("preserves ordinary Markdown and business JSON", () => {
    const content = [
      "## 接口结果",
      "",
      "```json",
      '{"status":"ok","recipient_name":"业务联系人"}',
      "```",
    ].join("\n");

    expect(sanitizeAssistantContent(content)).toBe(content);
  });
});
