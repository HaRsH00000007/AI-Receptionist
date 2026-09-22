import { ThemeToggle } from "@/app/ThemeToggle";
import { SignInPrompt } from "@/components/onboarding/SignInPrompt";
import { Logo } from "@/components/ui/Logo";

export default function OnboardingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-30 border-b border-line bg-bg/85 backdrop-blur-md">
        <div className="container-page flex h-16 items-center justify-between gap-4">
          <Logo />
          <div className="flex items-center gap-1 sm:gap-2">
            <SignInPrompt />
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main id="main" className="container-page py-8 sm:py-12">
        {children}
      </main>
    </div>
  );
}
