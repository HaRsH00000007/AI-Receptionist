import { SettingsFrame } from "@/components/app/settings/SettingsFrame";

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return <SettingsFrame>{children}</SettingsFrame>;
}
