import { ConfigProvider } from "antd";
import { act,fireEvent,render,screen,waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach,describe,expect,it,vi } from "vitest";
import { ApiError } from "../api";
import { RunPanel } from "../components/AgentRun";
const {api,resume}=vi.hoisted(()=>({api:{get:vi.fn(),post:vi.fn(),response:vi.fn()},resume:vi.fn()}));
vi.mock("../auth",()=>({useAuth:()=>({api})}));
vi.mock("../events",()=>({resumeRun:resume}));
const visible={id:"r",session_id:"s",status:"completed",event_seq:8,created_at:"2026-09-08T00:00:00Z",finished_at:null,content_hidden:false,agent_id:"a",configuration_id:"v",actual_scope:["space"],question:"依赖问题",answer:{facts:[{text:"PRIVATE-FACT",citation_ids:["cite"]}],inferences:[{text:"有条件的推断",citation_ids:["cite"]}],gaps:["没有生产部署证据"]},citations:[{id:"cite",evidence:{revision_id:"snap",resource_id:"source",source_id:"repo",source_revision:"commit-fixed",path:"src/main.go",start_line:2,end_line:4,kind:"code"},excerpt:"exact source",page_id:"page",revision_id:"wiki-v",space_id:"space",url:"/citations/snap?start=2&end=4"}],usage:{total_tokens:10},error_code:null,budget:{model_rounds:8,tool_calls:20,parallel_reads:2,seconds:180},rounds:1,tool_calls:1};
function mount(){render(<ConfigProvider theme={{token:{motion:false}}}><MemoryRouter><RunPanel runId="r" onRunChanged={vi.fn()} /></MemoryRouter></ConfigProvider>)}
beforeEach(()=>{vi.clearAllMocks();api.get.mockResolvedValue(visible);resume.mockImplementation(()=>new Promise(()=>{}))});
describe("real Agent run view",()=>{
 it("renders facts, inferences, evidence gaps and fixed citation locations",async()=>{mount();expect(await screen.findByText("PRIVATE-FACT")).toBeInTheDocument();expect(screen.getByText("推断")).toBeInTheDocument();expect(screen.getByText("没有生产部署证据")).toBeInTheDocument();expect(screen.getAllByRole("button",{name:/src\/main.go/}).length).toBeGreaterThan(0)});
 it("purges displayed answers immediately when current authorization fails",async()=>{mount();await screen.findByText("PRIVATE-FACT");api.get.mockRejectedValue(new ApiError(403,"forbidden"));act(()=>window.dispatchEvent(new Event("focus")));await waitFor(()=>expect(screen.queryByText("PRIVATE-FACT")).not.toBeInTheDocument());expect(await screen.findByText(/权限不足/)).toBeInTheDocument()});
 it("creates a new run when retrying and never rewrites old run",async()=>{api.post.mockResolvedValue({...visible,id:"new",status:"queued",answer:null,citations:[]});mount();await screen.findByText("PRIVATE-FACT");fireEvent.click(screen.getByRole("button",{name:"重新运行"}));await waitFor(()=>expect(api.post).toHaveBeenCalledWith("/runs/r/retry",{idempotency_key:expect.any(String)}))});
 it("keeps hidden run metadata but removes any returned sensitive payload",async()=>{api.get.mockResolvedValue({...visible,content_hidden:true});mount();expect(await screen.findByText(/已停止展示此回答/)).toBeInTheDocument();expect(screen.queryByText("PRIVATE-FACT")).not.toBeInTheDocument()});
});