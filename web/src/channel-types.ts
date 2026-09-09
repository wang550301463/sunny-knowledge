export interface ChannelConfig {
  id: string; name: string; bot_id: string; agent_id: string;
  agent_configuration_id: string; space_ids: string[]; version: number;
  enabled: boolean; status: string; tested_version: number; secret_configured: boolean;
}
export interface ChannelInput {
  base_version: number; name: string; bot_id: string; bot_secret: string;
  agent_id: string; space_ids: string[]; enabled: boolean;
}
export interface ChannelGroup {
  id: string; channel_id: string; chat_id: string; audience_id: string;
  space_ids: string[]; version: number; enabled: boolean;
  audience_version: number; desired_enabled: boolean;
  sync_state: "pending" | "synced"; sync_error: string;
}
export interface ChannelIdentity { channel_id: string; external_user_id: string; version: number; active: boolean }
export interface ChannelMessage { message_id: string; state: string; run_id: string; created_at: string }
export interface BindingProof { challenge_id: string; web_token: string }
