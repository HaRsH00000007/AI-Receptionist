import { ButtonLink, type ButtonSize } from "@/components/ui/Button";
import { MailIcon } from "@/components/ui/icons";
import { BRAND } from "@/lib/brand";

/** Email support when an address is configured; otherwise the contact page. */
export function SupportButton({
  size = "md",
  label = "Contact support",
}: {
  size?: ButtonSize;
  label?: string;
}) {
  return (
    <ButtonLink
      href={BRAND.supportEmail ? `mailto:${BRAND.supportEmail}` : "/contact"}
      variant="secondary"
      size={size}
      leadingIcon={<MailIcon size={16} />}
    >
      {label}
    </ButtonLink>
  );
}
