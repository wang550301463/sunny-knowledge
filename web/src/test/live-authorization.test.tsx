import { ConfigProvider } from 'antd';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { BrowserRouter, MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import { ReviewsPage } from '../pages/Reviews';
import { WikiPageDetail } from '../pages/Wiki';
const { api } = vi.hoisted(() => ({api:{get:vi.fn(),post:vi.fn()}}));
vi.mock('../auth',()=>({useAuth:()=>({api})}));
const revision={id:'r1',page_id:'p',number:1,base_revision:null,created_by:'u',created_at:'2026-09-08T00:00:00Z',publication_kind:'reviewed',content:{title:'Title',markdown:'HISTORIC-PRIVATE-MARKER',entity_type:'Module',claims:[],evidence:[],state:'valid'}};
const page={id:'p',space_id:'s',current_revision:'r2',revision_number:2,created_by:'u',created_at:'2026-09-08T00:00:00Z',revision:{...revision,id:'r2',number:2,content:{...revision.content,markdown:'CURRENT-MARKER'}}};
const proposal={id:'prop',page_id:'p',base_revision:null,status:'pending',proposed_by:'u',created_at:'2026-09-08T00:00:00Z',kind:'formal_change',reason:'Reason',content:{...revision.content,markdown:'PROPOSAL-PRIVATE-MARKER'}};
function review(){render(<ConfigProvider theme={{token:{motion:false}}}><BrowserRouter><ReviewsPage/></BrowserRouter></ConfigProvider>);}
function history(){render(<ConfigProvider theme={{token:{motion:false}}}><MemoryRouter initialEntries={['/pages/p']}><Routes><Route path='/pages/:pageId' element={<WikiPageDetail/>}/></Routes></MemoryRouter></ConfigProvider>);}
function normal(path:string){return Promise.resolve(path==='/spaces'?{items:[]}:path==='/reviews/prop'?proposal:path.startsWith('/reviews?')?{items:[proposal],next_cursor:null}:path==='/pages/p'?page:path==='/pages/p/revisions/r1'?revision:{items:[revision],next_cursor:'older'});}
async function openHistory(){history();fireEvent.click(await screen.findByRole('button',{name:/历史版本/}));fireEvent.click(await screen.findByRole('button',{name:'查看差异'}));}
beforeEach(()=>{vi.clearAllMocks();api.get.mockImplementation(normal);});
describe('live proposal authorization',()=>{
 it.each([null,'r1'])('does not open cached proposal body when new provenance is denied (base=%s)',async base=>{
  api.get.mockImplementation((path:string)=>Promise.resolve(path==='/spaces'?{items:[]}:{items:[{...proposal,base_revision:base}],next_cursor:null}));review();await screen.findByRole('button',{name:'查看提案'});
  let deny:(reason:unknown)=>void=()=>{};api.get.mockImplementation(()=>new Promise((_,reject)=>{deny=reject;}));fireEvent.click(screen.getByRole('button',{name:'查看提案'}));
  await waitFor(()=>expect(api.get).toHaveBeenCalledWith('/reviews/prop',expect.any(AbortSignal)));expect(document.body.textContent).not.toContain('PROPOSAL-PRIVATE-MARKER');
  deny(new ApiError(403,'forbidden'));await screen.findByText(/权限不足/);expect(document.body.textContent).not.toContain('PROPOSAL-PRIVATE-MARKER');
 });
 it('uses freshly authorized detail rather than the list payload',async()=>{
  api.get.mockImplementation(path=>path==='/reviews/prop'?Promise.resolve({...proposal,content:{...proposal.content,markdown:'FRESH-PROPOSAL-MARKER'}}):normal(path));review();fireEvent.click(await screen.findByRole('button',{name:'查看提案'}));
  await waitFor(()=>expect(document.body.textContent).toContain('FRESH-PROPOSAL-MARKER'));expect(document.body.textContent).not.toContain('PROPOSAL-PRIVATE-MARKER');
 });
 it.each([['批准并发布','approve'],['拒绝提案','reject']])('removes open details when %s loses authorization',async(label,action)=>{
  review();fireEvent.click(await screen.findByRole('button',{name:'查看提案'}));await waitFor(()=>expect(document.body.textContent).toContain('PROPOSAL-PRIVATE-MARKER'));
  api.post.mockRejectedValue(new ApiError(403,'forbidden'));fireEvent.change(screen.getByLabelText('审核意见'),{target:{value:'Review reason'}});fireEvent.click(screen.getByRole('button',{name:label}));
  await screen.findByText(/权限不足/);expect(api.post).toHaveBeenCalledWith(`/reviews/prop/${action}`,{reason:'Review reason'});expect(document.body.textContent).not.toContain('PROPOSAL-PRIVATE-MARKER');expect(screen.queryByLabelText('审核意见')).toBeNull();
 });
});
describe('live historical revision authorization',()=>{
 it('fetches the exact historical revision and hides cached body during pending and denial',async()=>{
  let deny:(reason:unknown)=>void=()=>{};api.get.mockImplementation(path=>path==='/pages/p/revisions/r1'?new Promise((_,reject)=>{deny=reject;}):normal(path));await openHistory();
  await waitFor(()=>expect(api.get).toHaveBeenCalledWith('/pages/p/revisions/r1',expect.any(AbortSignal)));expect(screen.queryByText('HISTORIC-PRIVATE-MARKER')).toBeNull();
  deny(new ApiError(403,'forbidden'));await screen.findByText(/权限不足/);expect(screen.queryByText('HISTORIC-PRIVATE-MARKER')).toBeNull();
 });
 it('removes selected historic body immediately when the next list page is loading, then denied',async()=>{
  await openHistory();await screen.findByText('HISTORIC-PRIVATE-MARKER');let deny:(reason:unknown)=>void=()=>{};api.get.mockImplementation(()=>new Promise((_,reject)=>{deny=reject;}));
  fireEvent.click(screen.getByRole('button',{name:'更早版本'}));expect(screen.queryByText('HISTORIC-PRIVATE-MARKER')).toBeNull();await waitFor(()=>expect(api.get).toHaveBeenCalledWith('/pages/p/revisions?cursor=older&limit=50',expect.any(AbortSignal)));
  deny(new ApiError(403,'forbidden'));await screen.findByText(/权限不足/);expect(screen.queryByText('HISTORIC-PRIVATE-MARKER')).toBeNull();
 });
 it('clears selected body when a rollback is denied',async()=>{
  await openHistory();await screen.findByText('HISTORIC-PRIVATE-MARKER');fireEvent.click(screen.getByRole('button',{name:/将此内容发布为新版本/}));api.post.mockRejectedValue(new ApiError(403,'forbidden'));
  const dialog=screen.getByRole('dialog',{name:/回退到版本/});fireEvent.change(within(dialog).getByLabelText('回退理由'),{target:{value:'Revert reason'}});fireEvent.click(within(dialog).getByRole('button',{name:'发布新版本'}));
  await screen.findByText(/权限不足/);expect(screen.queryByText('HISTORIC-PRIVATE-MARKER')).toBeNull();expect(screen.queryByLabelText('回退理由')).toBeNull();
 });
});