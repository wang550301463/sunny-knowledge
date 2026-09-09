import { SidebarSimple } from '@phosphor-icons/react';
import { NavLink } from 'react-router-dom';
import { Button } from '@/components/ui/Button';
import { mainNavItems, navItemEnd, settingsNavItems } from '@/app/shell/nav-config';
import { Brand } from '@/app/shell/Brand';
import { useAuthStore } from '@/features/auth/store';
import { cn } from '@/lib/cn';

export function Sidebar({ className }: { className?: string }): JSX.Element {
  const collapsed = useAuthStore((s) => s.sidebarCollapsed);
  const setCollapsed = useAuthStore((s) => s.setSidebarCollapsed);
  const permissions = useAuthStore((s) => s.permissions);
  const groups = [
    { title: '工作空间', items: mainNavItems.filter((item) => ['/', '/workbench'].includes(item.path)) },
    { title: '日常教学', items: mainNavItems.filter((item) => ['/schedule', '/attendance', '/attendance/leaves', '/students', '/lesson-history'].includes(item.path)) },
    { title: '教学资源', items: mainNavItems.filter((item) => ['/teachers/availabilities', '/courses', '/courses/class-groups'].includes(item.path)) },
    { title: '机构设置', items: settingsNavItems },
  ];
  return (
    <aside className={cn('studio-sidebar', collapsed ? 'w-[72px]' : 'w-[224px]', className)} aria-label="主导航">
      <Brand collapsed={collapsed} className={cn('h-[88px] shrink-0', collapsed ? 'justify-center' : 'px-5')} />
      <nav className="flex-1 space-y-5 overflow-y-auto px-3 pb-5">
        {groups.map((group) => {
          const items = group.items.filter((item) => !item.permission || permissions.includes(item.permission));
          if (!items.length) return null;
          return <div key={group.title}>
            {!collapsed && <p className="studio-section-label mb-2">{group.title}</p>}
            <div className="space-y-1">{items.map((item) => <NavLink key={item.path} to={item.path} end={navItemEnd(item.path)} title={collapsed ? item.label : undefined} aria-label={item.label}
              className={({ isActive }) => cn('studio-nav', isActive && 'studio-nav-active', collapsed && 'justify-center px-0')}>
              {({ isActive }) => <><item.icon className="h-[19px] w-[19px] shrink-0" weight={isActive ? 'duotone' : 'regular'} />{!collapsed && <span>{item.label}</span>}</>}
            </NavLink>)}</div>
          </div>;
        })}
      </nav>
      <div className="mx-3 flex items-center justify-between border-t border-black/5 py-3">
        {!collapsed && <span className="pl-2 text-[10px] tracking-wider text-muted-fg">给创造力，一点空间</span>}
        <Button variant="ghost" size="sm" className={collapsed ? 'w-full' : 'px-2'} onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? '展开侧边栏' : '折叠侧边栏'}><SidebarSimple className="h-[18px] w-[18px]" /></Button>
      </div>
    </aside>
  );
}