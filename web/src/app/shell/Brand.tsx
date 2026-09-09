import { PaintBrush } from '@phosphor-icons/react';
import { cn } from '@/lib/cn';

export function Brand({ collapsed = false, className }: { collapsed?: boolean; className?: string }): JSX.Element {
  return (
    <div className={cn('flex items-center gap-3', className)}>
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[14px] border border-white/90 bg-white/80 text-primary shadow-sm" aria-hidden>
        <PaintBrush className="h-6 w-6" weight="duotone" />
      </span>
      {!collapsed && <div><p className="text-[17px] font-semibold tracking-[.12em]">美术宝</p><p className="mt-0.5 text-[9px] font-medium tracking-[.19em] text-muted-fg">ART STUDIO</p></div>}
    </div>
  );
}