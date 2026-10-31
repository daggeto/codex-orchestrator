import { useEffect, useState } from 'react';
import Markdown from 'react-markdown';
import type { ThemedToken } from 'shiki';

function Code({ text, language }: { text: string; language: string }) {
  const [tokens, setTokens] = useState<ThemedToken[][]>();
  useEffect(() => {
    let active = true;
    setTokens(undefined);
    // Shiki's shorthand loads only the requested grammar and theme on demand.
    void import('shiki').then(({ codeToTokens }) => codeToTokens(text, { lang: language, theme: 'github-dark' }))
      .then(result => { if (active) setTokens(result.tokens); }).catch(() => { /* Unknown grammars use plain text. */ });
    return () => { active = false; };
  }, [text, language]);
  return <code>{tokens ? tokens.map((line, i) => <span key={i}>{line.map((token, j) => <span key={j} style={{ color: token.color }}>{token.content}</span>)}{'\n'}</span>) : text}</code>;
}
export function MessageContent({ text }: { text: string }) {
  return <Markdown skipHtml urlTransform={url => /^(https?:\/\/|mailto:|#|\/[^/])/.test(url) ? url : ''}
    components={{
      a: ({ children, href }) => <a href={href} rel="noreferrer noopener">{children}</a>,
      img: ({ alt }) => <span>{alt}</span>,
      code: ({ children, className }) => {
        const language = /language-([\w+-]+)/.exec(className || '')?.[1];
        return language ? <Code text={String(children).replace(/\n$/, '')} language={language} /> : <code>{children}</code>;
      },
    }}>{text}</Markdown>;
}
