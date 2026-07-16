const protocolMarker = /(?:^|\s)(?:analysis\s+)?to=(?:multi_tool_use\.parallel|functions\.[A-Za-z0-9_.-]+)\b/i;
const finalMarkdownBoundary = /#{1,6}\s+(?:现象|证据|判断|结论|建议|风险|下一步|最终结论)/;
const brokenNoticeMarker = /(?:鈿|璇佹嵁|[\uE000-\uF8FF])/;
const confidenceValue = /\b(low|medium|high)\b/i;


export function sanitizeAssistantContent(content: string): string {
  const noticeRepaired = repairVerificationNotice(content ?? "");
  let cleaned = noticeRepaired;

  while (true) {
    const marker = protocolMarker.exec(cleaned);
    if (!marker || marker.index === undefined) {
      return cleaned.trim();
    }

    const markerEnd = marker.index + marker[0].length;
    const suffix = cleaned.slice(markerEnd);
    const boundary = finalMarkdownBoundary.exec(suffix);
    if (boundary && boundary.index !== undefined) {
      const prefix = cleaned.slice(0, marker.index).trimEnd();
      cleaned = `${prefix}\n\n${suffix.slice(boundary.index)}`;
      continue;
    }

    const removalEnd = protocolPayloadEnd(cleaned, markerEnd);
    cleaned = `${cleaned.slice(0, marker.index)}${cleaned.slice(removalEnd)}`;
  }
}


function repairVerificationNotice(content: string): string {
  const lines = content.split("\n");
  const firstContentIndex = lines.findIndex((line) => line.trim().length > 0);
  if (firstContentIndex < 0) {
    return content;
  }

  const line = lines[firstContentIndex];
  const confidence = confidenceValue.exec(line)?.[1]?.toLowerCase();
  if (!line.trimStart().startsWith(">") || !confidence || !brokenNoticeMarker.test(line)) {
    return content;
  }

  lines[firstContentIndex] =
    `> ⚠️ 证据自检：置信度 ${confidence}，本次回答存在以下证据缺口，请谨慎采用：`;
  return lines.join("\n");
}


function protocolPayloadEnd(content: string, markerEnd: number): number {
  const lineEnd = content.indexOf("\n", markerEnd);
  const fallbackEnd = lineEnd >= 0 ? lineEnd : content.length;
  const jsonStart = content.indexOf("{", markerEnd);
  if (jsonStart < 0) {
    return fallbackEnd;
  }

  const jsonEnd = balancedJsonObjectEnd(content, jsonStart);
  return jsonEnd ?? fallbackEnd;
}


function balancedJsonObjectEnd(content: string, start: number): number | null {
  let depth = 0;
  let inString = false;
  let escaped = false;

  for (let index = start; index < content.length; index += 1) {
    const char = content[index];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === '"') {
        inString = false;
      }
      continue;
    }

    if (char === '"') {
      inString = true;
    } else if (char === "{") {
      depth += 1;
    } else if (char === "}") {
      depth -= 1;
      if (depth === 0) {
        return index + 1;
      }
    }
  }

  return null;
}
