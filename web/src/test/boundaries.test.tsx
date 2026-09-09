import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ApiClient, ApiError, errorMessage } from '../api';
import { validateCallback, safeReturnTo } from '../auth';
import { SafeMarkdown, ExactSource } from '../components/Content';
import { readEventStream } from '../events';
import { useResource } from '../hooks';

describe('authentication boundary', () => {
  it('requires state and code, rejects token fragment and duplicate state', () => {
    expect(() => validateCallback('https://knowledge.test/auth/callback?code=abc')).toThrow();
    expect(() => validateCallback('https://knowledge.test/auth/callback?code=a&state=b&state=c')).toThrow();
    expect(() => validateCallback('https://knowledge.test/auth/callback?code=a&state=b#access_token=secret')).toThrow();
    expect(() => validateCallback('https://knowledge.test/auth/callback?code=a&state=b')).not.toThrow();
    expect(safeReturnTo('//evil.test')).toBe('/chat');
    expect(safeReturnTo('/spaces/finance')).toBe('/spaces/finance');
  });
  it('refuses API calls without a token and never sends it in a URL', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{"items":[]}'));
    await expect(new ApiClient(async()=>null, fetcher).get('/spaces')).rejects.toMatchObject({status:401});
    expect(fetcher).not.toHaveBeenCalled();
    await new ApiClient(async()=> 'private-token', fetcher).get('/spaces');
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/spaces');
    expect(new Headers(fetcher.mock.calls[0][1].headers).get('Authorization')).toBe('Bearer private-token');
  });
  it('surfaces denied and stale revision responses instead of retrying a write', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{"error":{"code":"stale_revision","message":"Changed"}}',{status:409}));
    await expect(new ApiClient(async()=> 'token', fetcher).post('/pages/a/proposals', {base_revision:'old'})).rejects.toMatchObject({status:409});
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(errorMessage(new ApiError(403, 'forbidden'))).toContain('权限');
    expect(errorMessage(new ApiError(409, 'stale_revision'))).toContain('版本');
  });
});
describe('content boundary',()=> {
  it('does not render raw HTML, unsafe URLs, or remote tracking images',()=> {
    const {container} = render(<SafeMarkdown text={'<img src=x onerror=alert(1)>\n\n[bad](javascript:alert(1))\n\n![tracker](https://evil.test/image.png)\n\n**安全内容**'} />);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(screen.getByText('安全内容')).toBeVisible();
  });
  it('renders exact LF rows as text, preserves CR, excludes terminal empty row',()=> {
    const {container} = render(<ExactSource text={'first\r\n<script>alert(1)</script>\nlast\n'} startLine={2} endLine={3}/>);
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByText('<script>alert(1)</script>')).toBeVisible();
    expect(container.querySelectorAll('[data-line]')).toHaveLength(2);
    expect(container.querySelector('[data-line="2"]')).toBeTruthy();
  });
});
describe('scope and stream boundary',()=> {
  it('hides old scope data immediately and discards late responses',async()=> {
    let finishOld: (v:string)=>void=()=>{};
    function View({scope}:{scope:string}) { const r=useResource(scope,()=>scope==='old'?new Promise<string>(resolve=>{finishOld=resolve;}):Promise.resolve('new result')); return <div>{r.data ?? 'loading'}</div>; }
    const {rerender}=render(<View scope="old"/>); rerender(<View scope="new"/>);
    await screen.findByText('new result'); finishOld('private old result');
    await waitFor(()=>expect(screen.queryByText('private old result')).toBeNull());
  });
  it('parses split SSE chunks and retains ordered event cursor',async()=> {
    const events: unknown[]=[];
    const stream = new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('id: 1\nevent: content_delta\nda'));controller.enqueue(new TextEncoder().encode('ta: {"text":"hello"}\n\nid: 2\nevent: completed\ndata: {}\n\n'));controller.close();}});
    expect(await readEventStream(stream, event=>events.push(event))).toBe('2');
    expect(events).toEqual([{id:'1',type:'content_delta',data:{text:'hello'}},{id:'2',type:'completed',data:{}}]);
  });
});