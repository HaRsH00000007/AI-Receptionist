import { ThemeToggle } from "@/app/ThemeToggle";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { LockIcon } from "@/components/ui/icons";
import { Logo } from "@/components/ui/Logo";

export function AdminTopBar({ onLock }: { onLock?: () => void }) {
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-surface/85 backdrop-blur-md">
      <div className="container-page flex h-16 items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <Logo href="/admin" />
          <Badge tone="accent" className="max-sm:hidden">
            Operator console
          </Badge>
        </div>
        <div className="flex items-center gap-1.5">
          <ThemeToggle />
          {onLock && (
            <Button
              variant="secondary"
              size="sm"
              leadingIcon={<LockIcon size={15} />}
              onClick={onLock}
            >
              <span className="max-sm:sr-only">Lock console</span>
            </Button>
          )}
        </div>
      </div>
    </header>
  );
}
