import { ConfigProvider } from "antd";
import { fireEvent,render,screen,waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach,describe,expect,it,vi } from "vitest";
import { ApiError } from "../api";
import { ChannelBindingPage,MyChannelBindings } from "../pages/ChannelBindings";
const {api}=vi.hoisted(()=>({api:{get:vi.fn(),post:vi.fn(),delete:vi.fn()}}));
vi.mock("../auth",()=>({useAuth:()=>({api,user:{profile:{name:"当前平台用户"}}})}));
const token="a".repeat(43),proof="b".repeat(43);
beforeEach(()=>{vi.clearAllMocks();api.get.mockResolvedValue({items:[]});window.history.replaceState({},"","/channels/bind")});
function mount(bind=true){render(<ConfigProvider theme={{token:{motion:false}}}><MemoryRouter>{bind?<ChannelBindingPage/>:<MyChannelBindings/>}</MemoryRouter></ConfigProvider>)}
describe("two-way channel binding UI",()=>{
 it("consumes the URL fragment and never claims binding success before private confirmation",async()=>{window.history.replaceState({},"",`/channels/bind#challenge=challenge&token=${token}`);api.post.mockResolvedValue({confirmation_command:`/确认 challenge ${proof}`});mount();expect(window.location.hash).toBe("");fireEvent.click(screen.getByRole("button",{name:"确认关联当前账号"}));await waitFor(()=>expect(api.post).toHaveBeenCalledWith("/channel-bindings/claim",{challenge_id:"challenge",web_token:token}));expect(await screen.findByDisplayValue(`/确认 challenge ${proof}`)).toBeInTheDocument();expect(screen.getByText("等待企微私聊确认")).toBeInTheDocument();expect(screen.queryByText("绑定成功")).not.toBeInTheDocument();expect(JSON.stringify(sessionStorage)).not.toContain(token);expect(JSON.stringify(sessionStorage)).not.toContain(proof)});
 it("shows a safe restart instruction after first login lost the fragment",()=>{mount();expect(screen.getByText(/登录后，请回到企微/)).toBeInTheDocument();expect(screen.queryByRole("button",{name:"确认关联当前账号"})).not.toBeInTheDocument();expect(api.post).not.toHaveBeenCalled()});
 it("clears one-time proof after an expired or rejected claim without blind retry",async()=>{window.history.replaceState({},"",`/channels/bind#challenge=challenge&token=${token}`);api.post.mockRejectedValue(new ApiError(403,"forbidden"));mount();fireEvent.click(screen.getByRole("button",{name:"确认关联当前账号"}));expect(await screen.findByText(/已失效、已使用或无法验证/)).toBeInTheDocument();expect(document.body.textContent).not.toContain(token);expect(screen.queryByRole("button",{name:"确认关联当前账号"})).not.toBeInTheDocument()});
 it("unbinds only the selected current user's external identity and refreshes actual state",async()=>{api.get.mockResolvedValue({items:[{channel_id:"channel",external_user_id:"external",version:2,active:true}]});api.delete.mockResolvedValue({status:"unbound"});mount(false);fireEvent.click(await screen.findByRole("button",{name:"解除绑定 external"}));api.get.mockResolvedValue({items:[{channel_id:"channel",external_user_id:"external",version:3,active:false}]});fireEvent.click(await screen.findByRole("button",{name:"确认解除"}));await waitFor(()=>expect(api.delete).toHaveBeenCalledWith("/channels/channel/bindings/external"));expect(await screen.findByText("已解除")).toBeInTheDocument()});
});
