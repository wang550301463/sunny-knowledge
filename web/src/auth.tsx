import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Log, UserManager, WebStorageStateStore } from 'oidc-client-ts';
import type { User } from 'oidc-client-ts';
import { ApiClient } from './api';
Log.setLevel(Log.NONE);
export function safeReturnTo(value:unknown):string {
  if(typeof value!=='string'||!value.startsWith('/')||value.startsWith('//')||value.includes('\\')||/[\x00-\x1f]/.test(value)||value.startsWith('/auth/'))return '/chat';
  return value;
}
export function validateCallback(raw:string) {
  const url=new URL(raw);
  if(url.hash||url.searchParams.getAll('state').length!==1||!url.searchParams.get('state')||url.searchParams.getAll('code').length!==1||!url.searchParams.get('code')||url.searchParams.has('access_token')||url.searchParams.has('id_token')||url.searchParams.has('error'))throw new Error('invalid_callback');
}
export function createAuthManager(origin=window.location.origin) {
  return new UserManager({authority:`${origin}/idp/realms/knowledge`,client_id:'knowledge-web',redirect_uri:`${origin}/auth/callback`,post_logout_redirect_uri:`${origin}/`,response_type:'code',scope:'openid profile email knowledge:read knowledge:write',disablePKCE:false,automaticSilentRenew:true,includeIdTokenInSilentRenew:false,loadUserInfo:false,monitorSession:false,staleStateAgeInSeconds:600,accessTokenExpiringNotificationTimeInSeconds:60,userStore:new WebStorageStateStore({store:window.sessionStorage,prefix:'knowledge.session.'}),stateStore:new WebStorageStateStore({store:window.sessionStorage,prefix:'knowledge.oidc.'})});
}
interface AuthContextValue { user:User|null; loading:boolean; error:string|null; api:ApiClient; login:()=>Promise<void>; logout:()=>Promise<void> }
const AuthContext=createContext<AuthContextValue|null>(null);
export function AuthProvider({children}:{children:ReactNode}) {
  const [manager]=useState(createAuthManager);
  const [user,setUser]=useState<User|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState<string|null>(null);
  const [api]=useState(()=>new ApiClient(async()=>{let current=await manager.getUser(); if(current&&current.expires_in!==undefined&&current.expires_in<30){try{current=await manager.signinSilent();}catch{await manager.removeUser();return null;}}return current&&!current.expired?current.access_token:null;}));
  useEffect(()=>{
    let active=true;
    const loaded=(value:User)=>{if(active){setUser(value);setError(null);}};
    const unloaded=()=>{if(active)setUser(null);};
    const expired=()=>{void manager.removeUser();};
    const failed=()=>{if(active)setError('会话刷新失败，请重新登录。');void manager.removeUser();};
    manager.events.addUserLoaded(loaded);manager.events.addUserUnloaded(unloaded);manager.events.addAccessTokenExpired(expired);manager.events.addSilentRenewError(failed);
    void (async()=>{
      try {
        await manager.clearStaleState();
        if(window.location.pathname==='/auth/callback') {
          const callback=window.location.href;
          window.history.replaceState({},'', '/auth/callback');
          validateCallback(callback);
          const next=await manager.signinRedirectCallback(callback);
          if(active)setUser(next);
          const state=next.state as {returnTo?:unknown}|undefined;
          window.location.replace(safeReturnTo(state?.returnTo));
        } else { const stored=await manager.getUser(); if(stored&&!stored.expired){if(active)setUser(stored);}else if(stored){try{const refreshed=await manager.signinSilent();if(active)setUser(refreshed);}catch{await manager.removeUser();}} }
      }catch { await manager.removeUser();if(active)setError('登录校验失败或身份服务不可用，请重新发起登录。'); }
      finally { if(active)setLoading(false); }
    })();
    return()=>{active=false;manager.events.removeUserLoaded(loaded);manager.events.removeUserUnloaded(unloaded);manager.events.removeAccessTokenExpired(expired);manager.events.removeSilentRenewError(failed);};
  },[manager]);
  const login=async()=>{setError(null);try{const bytes=crypto.getRandomValues(new Uint8Array(32));const nonce=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');await manager.signinRedirect({nonce,state:{returnTo:safeReturnTo(window.location.pathname+window.location.search)}});}catch{setError('无法连接身份服务，请稍后重试。');}};
  const logout=async()=>{manager.stopSilentRenew();try{await manager.revokeTokens(['refresh_token']);}catch{/* Local logout still proceeds; no credential is logged. */}await manager.removeUser();try{await manager.signoutRedirect({post_logout_redirect_uri:window.location.origin+'/'});}catch{setError('本机会话已清除；身份服务退出失败，可重新登录。');}};
  return <AuthContext.Provider value={{user,loading,error,api,login,logout}}>{children}</AuthContext.Provider>;
}
export function useAuth(){const value=useContext(AuthContext);if(!value)throw new Error('AuthProvider required');return value;}