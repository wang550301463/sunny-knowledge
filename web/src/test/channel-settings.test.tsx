import { ConfigProvider } from "antd";
import { fireEvent,render,screen,waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach,describe,expect,it,vi } from "vitest";
import { ApiError } from "../api";
import { ChannelConnection,ChannelGroups } from "../pages/Channels";
import type { ChannelConfig,ChannelGroup } from "../channel-types";
const {api}=vi.hoisted(()=>({api:{get:vi.fn(),post:vi.fn(),put:vi.fn()}}));
vi.mock("../auth",()=>({useAuth:()=>({api})}));
const config:ChannelConfig={id:"c",name:"机器人",bot_id:"bot",agent_id:"a",agent_configuration_id:"v",space_ids:["s"],version:3,enabled:false,status:"disabled",tested_version:0,secret_configured:true};
const group:ChannelGroup={id:"g",channel_id:"c",chat_id:"chat",audience_id:"audience",space_ids:["s"],version:4,enabled:false,audience_version:2,desired_enabled:true,sync_state:"pending",sync_error:"audience_unavailable"};
beforeEach(()=>{vi.clearAllMocks();api.get.mockResolvedValue({items:[]})});
function mount(children:React.ReactNode){render(<ConfigProvider theme={{token:{motion:false}}}><MemoryRouter>{children}</MemoryRouter></ConfigProvider>)}
describe("actual channel lifecycle",()=>{
 it("treats connection test acceptance as pending and never as a passed handshake",async()=>{api.post.mockResolvedValue({status:"test_pending"});const refresh=vi.fn();mount(<ChannelConnection config={config} onChanged={refresh}/>);fireEvent.click(screen.getByRole("button",{name:"检测连接"}));await waitFor(()=>expect(api.post).toHaveBeenCalledWith("/channels/c/test",{base_version:3}));expect(screen.getByRole("button",{name:"启用渠道"})).toBeDisabled();expect(screen.queryByText("连接检测通过")).not.toBeInTheDocument();expect(refresh).toHaveBeenCalled()});
 it("enables only the tested reviewed CAS without resending a stored secret",async()=>{api.put.mockResolvedValue({...config,version:4,enabled:true,status:"connecting",tested_version:4});mount(<ChannelConnection config={{...config,status:"tested",tested_version:3}} onChanged={vi.fn()}/>);fireEvent.click(screen.getByRole("button",{name:"启用渠道"}));fireEvent.click(await screen.findByRole("button",{name:"确认启用"}));await waitFor(()=>expect(api.put).toHaveBeenCalledWith("/channels/c",{base_version:3,name:"机器人",bot_id:"bot",bot_secret:"",agent_id:"a",space_ids:["s"],enabled:true}));expect(screen.queryByText("已连接")).not.toBeInTheDocument()});
 it("keeps a pending group disabled and refreshes durable state after failed reconciliation",async()=>{api.get.mockResolvedValue({items:[group]});api.post.mockRejectedValue(new ApiError(503,"channel_unavailable"));mount(<ChannelGroups config={config}/>);expect(await screen.findByText("授权同步待完成，当前禁用")).toBeInTheDocument();const before=api.get.mock.calls.length;fireEvent.click(screen.getByRole("button",{name:"继续对账 chat"}));await waitFor(()=>expect(api.post).toHaveBeenCalledWith("/channels/c/groups/chat/reconcile",{base_version:4}));await waitFor(()=>expect(api.get.mock.calls.length).toBeGreaterThan(before));expect(await screen.findByText("授权同步待完成，当前禁用")).toBeInTheDocument();expect(screen.queryByText("已授权群聊")).not.toBeInTheDocument()});
});
