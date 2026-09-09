import { describe,expect,it } from "vitest";
import { channelConfig,groupState,parseBindingLink } from "../channel-api";
describe("channel UI boundaries",()=>{
 it("drops every secret field returned by an invalid server response",()=>{const result=channelConfig({id:"c",name:"研发机器人",bot_id:"bot",agent_id:"a",agent_configuration_id:"v",space_ids:["s"],version:1,enabled:false,status:"disabled",tested_version:0,secret_configured:true,bot_secret:"PRIVATE-SECRET",secret_cipher:"PRIVATE-CIPHER"});expect(JSON.stringify(result)).not.toContain("PRIVATE")});
 it("never labels a pending audience operation enabled",()=>{expect(groupState({sync_state:"pending",enabled:true,desired_enabled:true})).toEqual({label:"授权同步待完成，当前禁用",usable:false});expect(groupState({sync_state:"synced",enabled:true,desired_enabled:true}).usable).toBe(true)});
 it("parses one-time binding proof only from a strictly shaped fragment",()=>{const token="a".repeat(43);expect(parseBindingLink(`#challenge=id&token=${token}`)).toEqual({challenge_id:"id",web_token:token});expect(()=>parseBindingLink(`#challenge=id&token=${token}&token=duplicate`)).toThrow();expect(()=>parseBindingLink("#challenge=id&access_token=secret")).toThrow()});
});
