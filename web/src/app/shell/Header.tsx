import { List, MagnifyingGlass } from '@phosphor-icons/react';
import { useLocation } from 'react-router-dom';
import { getNavLabel } from '@/app/shell/nav-config';
import { Button } from '@/components/ui/Button';
import { UserMenu } from '@/features/auth/components/UserMenu';

type HeaderProps = { onOpenSearch?: () => void; onOpenMobileNav?: () => void };

export function Header({ onOpenSearch, onOpenMobileNav }: HeaderProps): JSX.Element {
  const { pathname } = useLocation();
  return (
    <header className="studio-toolbar z-30 flex h-[66px] shrink-0 items-center justify-between gap-3 px-4 lg:px-7">
      <div className="flex min-w-0 items-center gap-3">
        <Button variant="ghost" size="sm" className="px-2 lg:hidden" onClick={onOpenMobileNav} aria-label="打开导航菜单"><List className="h-5 w-5" /></Button>
        <nav aria-label="面包屑" className="flex items-center gap-3 text-[13px]"><span className="hidden text-muted-fg md:inline">美术宝</span><span className="hidden text-border md:inline" aria-hidden>/</span><span className="truncate font-medium">{getNavLabel(pathname)}</span></nav>
      </div>
      <div className="flex shrink-0 items-center gap-2 sm:gap-4">
        <Button variant="ghost" size="sm" className="gap-2 rounded-lg border border-white/90 bg-white/55 px-2 sm:min-w-44 sm:justify-start sm:px-3" onClick={onOpenSearch} aria-label="全局搜索">
          <MagnifyingGlass className="h-4 w-4 text-muted-fg" /><span className="hidden text-xs text-muted-fg sm:inline">搜索学员、课程…</span><kbd className="ml-auto hidden rounded bg-black/[.035] px-1.5 py-0.5 text-[10px] text-muted-fg sm:inline">⌘ K</kbd>
        </Button>
        <div className="hidden h-5 w-px bg-border/70 sm:block" /><UserMenu />
      </div>
    </header>
  );
}