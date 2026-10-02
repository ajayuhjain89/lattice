import { ButtonLink } from "@/components/primitives/Button";
import { StateBlock } from "@/components/primitives/StateBlock";

export interface NotAvailableProps {
  label: string;
  title: string;
  body: string;
  action?: { href: string; label: string };
  headingLevel?: "h1" | "h2";
}

/**
 * The one "not available" state. A resource that does not exist and one the viewer may
 * not read render identically (SEC-5): same copy, same layout, same timing path. The
 * boundary family of StateBlock.
 */
export function NotAvailable({ label, title, body, action, headingLevel = "h1" }: NotAvailableProps) {
  return (
    <StateBlock
      family="boundary"
      label={label}
      heading={title}
      headingLevel={headingLevel}
      actions={action && <ButtonLink href={action.href}>{action.label}</ButtonLink>}
    >
      <p>{body}</p>
    </StateBlock>
  );
}
