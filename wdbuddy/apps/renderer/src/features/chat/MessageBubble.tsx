import React from 'react';
import type { Message } from '@wdbuddy/shared-types';

/** 消息气泡（§4.4：用户右对齐气泡；Agent 左对齐 Markdown 流式渲染） */
export function MessageBubble({ message }: { message: Message }): React.ReactElement {
  const isUser = message.role === 'user';
  const isSystem = message.role === 'system';

  if (isSystem) {
    // 系统消息分级：✅ 成功（绿色醒目卡片）/ ⚠ 失败（红色）/ 其他（中性）
    const isSuccess = message.content.trimStart().startsWith('✅');
    const isError = message.content.trimStart().startsWith('⚠');
    const cls = isSuccess
      ? 'wb-msg wb-msg--system wb-msg--done'
      : isError
        ? 'wb-msg wb-msg--system wb-msg--error'
        : 'wb-msg wb-msg--system';
    return <div className={cls}>{message.content}</div>;
  }

  return (
    <div className={`wb-msg ${isUser ? 'wb-msg--user' : 'wb-msg--agent'}`}>
      <div className="wb-msg__avatar">{isUser ? '👤' : '🤖'}</div>
      <div className={`wb-msg__bubble ${isUser ? 'wb-msg__bubble--user' : ''}`}>
        {/* 轻量 Markdown 渲染（标题/粗体/代码块/列表） */}
        <MarkdownLite text={message.content} />
      </div>
    </div>
  );
}

/** 轻量 Markdown：不引入第三方库，覆盖标题/粗体/行内代码/代码块/列表/引用 */
export function MarkdownLite({ text }: { text: string }): React.ReactElement {
  const blocks = text.split(/```/);
  return (
    <div className="wb-md">
      {blocks.map((block, i) =>
        i % 2 === 1 ? (
          <pre key={i} className="wb-md__code">
            <code>{block.replace(/^\w+\n/, '')}</code>
          </pre>
        ) : (
          <React.Fragment key={i}>{renderInline(block)}</React.Fragment>
        )
      )}
    </div>
  );
}

function renderInline(text: string): React.ReactNode[] {
  const lines = text.split('\n');
  return lines.map((line, i) => {
    // 标题
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const level = h[1]!.length;
      return (
        <div key={i} className={`wb-md__h wb-md__h--${level}`}>
          {inline(h[2]!)}
        </div>
      );
    }
    // 列表
    const li = line.match(/^\s*[-*·]\s+(.*)$/);
    if (li) {
      return (
        <div key={i} className="wb-md__li">
          <span className="wb-md__bullet">•</span>
          {inline(li[1]!)}
        </div>
      );
    }
    if (line.match(/^\s*\d+[.、)]\s+/)) {
      return (
        <div key={i} className="wb-md__li">
          {inline(line)}
        </div>
      );
    }
    // 引用
    if (line.startsWith('> ')) {
      return (
        <div key={i} className="wb-md__quote">
          {inline(line.slice(2))}
        </div>
      );
    }
    if (line.trim() === '') return <div key={i} className="wb-md__br" />;
    return <div key={i}>{inline(line)}</div>;
  });
}

function inline(s: string): React.ReactNode {
  // 粗体 **x** 与行内代码 `x`
  const parts = s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**')) {
      return <strong key={i}>{p.slice(2, -2)}</strong>;
    }
    if (p.startsWith('`') && p.endsWith('`') && p.length > 1) {
      return <code key={i} className="wb-md__inline-code">{p.slice(1, -1)}</code>;
    }
    return <React.Fragment key={i}>{p}</React.Fragment>;
  });
}
