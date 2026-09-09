import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import { PageEditor } from '../pages/Wiki';
import { ModelForm } from '../pages/Models';
import { GrantEditor } from '../pages/Spaces';
import { createAuthManager } from '../auth';
import type { ModelRecord, WikiPage } from '../types';
const {api}=vi.hoisted(()=>({api:{get:vi.fn(),post:vi.fn(),put:vi.fn()}}));
vi.mock('../auth',async importOriginal=>({...await importOriginal<typeof import('../auth')>(),useAuth:()=>({api})}));
beforeEach(()=>{vi.clearAllMocks();});
const page:WikiPage={id:'page:test',space_id:'space:one',current_revision:'revision-old',revision_number:1,created_by:'user',created_at:'2026-09-08T00:00:00Z',revision:{id:'revision-old',page_id:'page:test',number:1,base_revision:null,created_by:'user',created_at:'2026-09-08T00:00:00Z',publication_kind:'reviewed',content:{title:'Original',markdown:'Original content',entity_type:'Module',claims:[],evidence:[],state:'valid'}}};
const model:ModelRecord={id:'model',configuration_id:'config-old',name:'Chat model',provider:'openai',provider_model:'model-id',base_url:'https://provider.example/v1',capability:'chat',dimensions:null,request_dimensions:false,max_input_chars:98765,max_batch_size:44,max_output_tokens:1234,max_response_bytes:4_000_000,timeout_seconds:31,max_retries:1,concurrency_per_replica:3,max_queue_per_replica:8,chat_token_parameter:'max_tokens',version:2,state:'active',has_credential:true,test_state:'untested',capabilities:{}};
describe('real workflow requests',()=>{
  it('submits a proposal with frozen base revision without opening advanced evidence fields',async()=>{
    const saved=vi.fn();api.post.mockResolvedValue({id:'proposal'});
    render(<BrowserRouter><PageEditor open page={page} spaceId={page.space_id} onClose={vi.fn()} onSaved={saved}/></BrowserRouter>);
    const user=userEvent.setup();await user.type(screen.getByLabelText('修改理由'),'Reviewed change');await user.click(screen.getByRole('button',{name:'提交审核'}));
    await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/pages/page%3Atest/proposals',expect.objectContaining({base_revision:'revision-old',content:page.revision?.content,reason:'Reviewed change'})));
    expect(saved).toHaveBeenCalled();
  });
  it('keeps a rejected stale edit visible and does not silently retry',async()=>{
    api.post.mockRejectedValue(new ApiError(409,'stale_revision'));const saved=vi.fn();
    render(<BrowserRouter><PageEditor open page={page} spaceId={page.space_id} onClose={vi.fn()} onSaved={saved}/></BrowserRouter>);
    const user=userEvent.setup();await user.type(screen.getByLabelText('修改理由'),'My change');await user.click(screen.getByRole('button',{name:'提交审核'}));
    expect(await screen.findByText(/版本已变化或记录冲突/)).toBeVisible();expect(screen.getByLabelText('正文（Markdown）')).toHaveValue('Original content');expect(api.post).toHaveBeenCalledTimes(1);expect(saved).not.toHaveBeenCalled();
  });
  it('preserves advanced model config and clears the credential even after failed save',async()=>{
    api.put.mockRejectedValue(new ApiError(403,'forbidden'));render(<ModelForm model={model} onClose={vi.fn()} onSaved={vi.fn()}/>);
    const user=userEvent.setup();const input=screen.getByLabelText('更新 API 密钥（留空保留）');await user.type(input,'private-test-key');await user.click(screen.getByRole('button',{name:'保存模型'}));
    await waitFor(()=>expect(api.put).toHaveBeenCalled());expect(api.put.mock.calls[0][1]).toMatchObject({base_configuration_id:'config-old',credential:'private-test-key',config:{max_input_chars:98765,max_output_tokens:1234,timeout_seconds:31,chat_token_parameter:'max_tokens'}});expect(input).toHaveValue('');expect(await screen.findByText(/权限不足/)).toBeVisible();
  });
  it('shows resource-grant permission denial without an editable empty policy',async()=>{
    api.get.mockRejectedValue(new ApiError(403,'forbidden'));render(<GrantEditor spaceId="private"/>);
    expect(await screen.findByText(/权限不足/)).toBeVisible();expect(screen.queryByRole('button',{name:'保存授权'})).toBeNull();
  });
  it('rejects a callback whose state was never issued without exchanging the code',async()=>{
    const fetcher=vi.spyOn(globalThis,'fetch');const manager=createAuthManager('https://knowledge.test');
    await expect(manager.signinRedirectCallback('https://knowledge.test/auth/callback?code=untrusted&state=not-issued')).rejects.toThrow('No matching state');expect(fetcher).not.toHaveBeenCalled();
  });
});