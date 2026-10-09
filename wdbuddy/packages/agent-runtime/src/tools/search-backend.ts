/**
 * search.web 检索后端 — DuckDuckGo Instant Answer API（免 Key）
 * 轻量级：返回摘要 + 相关主题列表，供 LLM 作为线索（深度调研可多轮查询）
 */
const DDG_API = 'https://api.duckduckgo.com/';
const TIMEOUT_MS = 10_000;

export async function ddgSearch(query: string): Promise<string> {
  const url = `${DDG_API}?q=${encodeURIComponent(query)}&format=json&no_html=1&skip_disambig=1`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, { signal: ctrl.signal, headers: { 'User-Agent': 'WDBuddy/1.0' } });
    if (!res.ok) {
      return `搜索服务返回 ${res.status}，请稍后重试或基于已有知识作答`;
    }
    const data = (await res.json()) as {
      AbstractText?: string;
      AbstractURL?: string;
      Heading?: string;
      RelatedTopics?: Array<{ Text?: string; FirstURL?: string }>;
    };
    const lines: string[] = [`搜索：${query}`];
    if (data.Heading) lines.push(`主题：${data.Heading}`);
    if (data.AbstractText) {
      lines.push(`摘要：${data.AbstractText}`);
      if (data.AbstractURL) lines.push(`来源：${data.AbstractURL}`);
    }
    const related = (data.RelatedTopics ?? [])
      .filter((t) => t.Text)
      .slice(0, 8)
      .map((t) => `- ${t.Text}${t.FirstURL ? `（${t.FirstURL}）` : ''}`);
    if (related.length > 0) lines.push('相关主题：', ...related);
    if (lines.length <= 1) {
      return `搜索"${query}"无直接结果，建议换个关键词或基于已有知识作答`;
    }
    return lines.join('\n');
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return `搜索失败（${msg}）：网络不可用或超时，请基于已有知识作答并说明未联网核实`;
  } finally {
    clearTimeout(timer);
  }
}
