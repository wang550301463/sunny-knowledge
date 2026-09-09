import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { login, logout } from '@/features/auth/api';
import { useAuthStore } from '@/features/auth/store';
import { LoginPage } from '@/features/auth/pages/LoginPage';
import { UserMenu } from '../UserMenu';
vi.mock('@/features/auth/api', () => ({ login: vi.fn(), logout: vi.fn() }));
const user = { id: 2, name: '老师', username: 'teacher', roles: ['TEACHER'], permissions: [], branches: [] };
beforeEach(() => { useAuthStore.getState().clearAuth(); vi.clearAllMocks(); vi.mocked(login).mockResolvedValue({ accessToken: 'test-token', refreshToken: 'refresh', user }); vi.mocked(logout).mockResolvedValue(undefined); });
afterEach(cleanup);
function setup(view: 'login' | 'menu') {
  const client = new QueryClient(); client.setQueryData(['private-records'], ['previous-account-student']);
  render(<QueryClientProvider client={client}><MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={['/entry']}><Routes><Route path="/entry" element={view === 'login' ? <LoginPage /> : <UserMenu />} /><Route path="/" element={<p>工作空间</p>} /><Route path="/login" element={<p>已退出</p>} /></Routes></MemoryRouter></QueryClientProvider>);
  return client;
}
describe('authentication cache boundary', () => {
  it('clears previous-account records before entering a new signed-in session', async () => {
    const client = setup('login'); await userEvent.type(screen.getByLabelText('账号'), 'teacher'); await userEvent.type(screen.getByLabelText('密码'), 'TestPassword9'); await userEvent.click(screen.getByRole('button', { name: '进入工作空间' })); await screen.findByText('工作空间'); expect(client.getQueryData(['private-records'])).toBeUndefined(); expect(useAuthStore.getState().user?.id).toBe(2);
  });
  it('discards private cache when signing out, including a failed logout request', async () => {
    useAuthStore.getState().setAuth({ accessToken: 'old', refreshToken: 'old', user }); vi.mocked(logout).mockRejectedValue(new Error('offline')); const client = setup('menu'); await userEvent.click(screen.getByRole('button', { name: '用户菜单' })); await userEvent.click(screen.getByRole('menuitem', { name: '退出登录' })); await waitFor(() => expect(screen.getByText('已退出')).toBeInTheDocument()); expect(client.getQueryData(['private-records'])).toBeUndefined(); expect(useAuthStore.getState().accessToken).toBeNull();
  });
});